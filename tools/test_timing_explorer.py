# SPDX-License-Identifier: Apache-2.0
"""End-to-end tests for the host Timing Boundary Explorer CLI and RTL bridge."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tools.diff_minimizer import eval_ref_vs_dut, minimize_divergence, replay_minimized_case
from tools.protocol_asm import (
    analyze_timing_bounds,
    assemble,
    build_i2c_stretch_probe_program,
    build_i2c_write_byte_program,
    build_spi_target_responder_program,
    build_uart_tx_program,
    check_timing_contract,
    insn,
    predict_dynamic_contract,
)
from tools.timing_explorer import (
    IcarusSimTransport,
    ProtocolClient,
    ProtocolError,
    classify_boundary,
    execute_replay,
    format_report_markdown,
    read_jsonl,
    replay_records,
    run_i2c_stretch_sweep,
    run_spi_boundary_exploration,
    run_uart_period_sweep,
    write_jsonl,
)


class TimingExplorerTests(unittest.TestCase):
    def test_classify_boundary_distinguishes_bracketed_and_censored_states(self) -> None:
        self.assertEqual(classify_boundary({2: False, 3: True, 4: True})["status"], "bracketed")
        self.assertEqual(classify_boundary({2: False, 3: True})["boundary_low_cycles"], 3)
        self.assertEqual(classify_boundary({2: True, 3: True})["status"], "censored_all_pass")
        self.assertIsNone(classify_boundary({2: True, 3: True})["boundary_low_cycles"])
        self.assertEqual(classify_boundary({2: False, 3: False})["status"], "all_fail_unbracketed")
        self.assertIsNone(classify_boundary({2: False, 3: False})["boundary_low_cycles"])
        self.assertEqual(classify_boundary({2: True, 3: False, 4: True})["status"], "non_monotone")

    def test_rtl_spi_sweep_binary_search_and_replay(self) -> None:
        with IcarusSimTransport() as transport:
            transport.reset(1)
            client = ProtocolClient(transport)

            # 1. Bracketed 23 ns target -> boundary at low_cycles = 3
            transport.configure_target(0, delay_ns=23, spi_resp=0xAA)
            sweep_23 = run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=6, method="sweep", delay_ns=23
            )
            replayed_23 = replay_records(sweep_23)
            self.assertTrue(replayed_23["matches_stored_summary"])
            self.assertEqual(replayed_23["boundary"]["status"], "bracketed")
            self.assertEqual(replayed_23["boundary"]["boundary_low_cycles"], 3)

            bin_23 = run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=6, method="binary", delay_ns=23
            )
            replayed_bin = replay_records(bin_23)
            self.assertEqual(replayed_bin["boundary"]["boundary_low_cycles"], 3)
            self.assertLess(len(replayed_bin["trials"]), len(replayed_23["trials"]))

            # 2. Shifted 35 ns target -> boundary shifts cleanly to low_cycles = 4
            transport.configure_target(0, delay_ns=35, spi_resp=0xAA)
            bin_35 = run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=6, method="binary", delay_ns=35
            )
            self.assertEqual(replay_records(bin_35)["boundary"]["boundary_low_cycles"], 4)

            # 3. Censored fast target (15 ns passes even at L=2=20 ns)
            transport.configure_target(0, delay_ns=15, spi_resp=0xAA)
            censored = run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=5, method="binary", delay_ns=15
            )
            rep_cen = replay_records(censored)
            self.assertEqual(rep_cen["boundary"]["status"], "censored_all_pass")
            self.assertIsNone(rep_cen["boundary"]["boundary_low_cycles"])

            # 4. Unbracketed slow target (95 ns fails across L=2..5)
            transport.configure_target(0, delay_ns=95, spi_resp=0xAA)
            unbr = run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=5, method="binary", delay_ns=95
            )
            rep_unbr = replay_records(unbr)
            self.assertEqual(rep_unbr["boundary"]["status"], "all_fail_unbracketed")
            self.assertIsNone(rep_unbr["boundary"]["boundary_low_cycles"])

            # 5. JSONL round-trip + report generation + tamper rejection
            with tempfile.TemporaryDirectory() as tmp:
                trace_path = Path(tmp) / "trace.jsonl"
                write_jsonl(trace_path, sweep_23)
                loaded = read_jsonl(trace_path)
                rechecked = replay_records(loaded)
                self.assertEqual(rechecked["boundary"]["boundary_low_cycles"], 3)
                md = format_report_markdown(rechecked)
                self.assertIn("boundary low cycles = `3`", md)

                loaded[2]["passed"] = not loaded[2]["passed"]
                with self.assertRaises(ProtocolError):
                    replay_records(loaded)

    def test_rtl_uart_i2c_and_custom_loop_program(self) -> None:
        with IcarusSimTransport() as transport:
            transport.reset(1)
            client = ProtocolClient(transport)

            # UART TX example
            transport.configure_target(1)
            client.load_program(build_uart_tx_program(0xA3, bit_cycles=8, pin=0))
            uart_res = client.run_trial(0)
            self.assertEqual(uart_res["eng_state"], "ENG_HALTED")
            self.assertTrue(uart_res["sim_observations"]["uart_mon_seen"])
            self.assertEqual(uart_res["sim_observations"]["uart_mon_byte"], 0xA3)

            # I2C ACK & NACK over UART loader
            i2c_words = build_i2c_write_byte_program(0xA0, half_cycles=4, ack_tag=0xAC, nack_tag=0xDE)
            client.load_program(i2c_words)
            transport.configure_target(2, i2c_ack=1)
            ack_res = client.run_trial(0)
            self.assertFalse(ack_res["sim_observations"]["i2c_od_fault"])
            self.assertEqual(ack_res["sim_observations"]["i2c_rx_byte"], 0xA0)
            self.assertEqual(ack_res["events"][0]["tag"], 0xAC)

            transport.configure_target(2, i2c_ack=0)
            nack_res = client.run_trial(0)
            self.assertEqual(nack_res["events"][0]["tag"], 0xDE)

            # Custom assembled loop with symbolic labels + MARK timestamps
            custom = assemble(
                [
                    "SETCOUNT 0, 4",
                    "loop_top:",
                    "WAIT_PARAM 1",
                    "MARK 0x42, 0",
                    "LOOP 0, loop_top",
                    "HALT 0",
                ]
            )
            client.load_program(custom)
            loop_res = client.run_trial(3)
            self.assertEqual(loop_res["eng_state"], "ENG_HALTED")
            self.assertEqual([e["tag"] for e in loop_res["events"]], [0x42, 0x42, 0x42, 0x42])

            # OP_PATCH_WORD changes MARK tag at addr 2 in 1 UART command instead of full reload
            client.patch_word(2, insn("MARK", 0x99))
            patched_res = client.run_trial(3)
            self.assertEqual([e["tag"] for e in patched_res["events"]], [0x99, 0x99, 0x99, 0x99])

    def test_v11_baud_sel_multiproto_sweeps_replay_and_diff_minimizer(self) -> None:
        with IcarusSimTransport() as transport:
            transport.reset(1)
            client = ProtocolClient(transport)

            # 1. Runtime baud selector ui_in[2:1]: sel=1 (12MHz/115200=104) & sel=3 (50MHz/115200=434)
            transport.set_baud_sel(1)
            self.assertEqual(client.ping()["prog_depth"], 64)
            transport.set_baud_sel(3)
            self.assertEqual(client.ping()["version"], 1)
            transport.set_baud_sel(0)

            # 2. Experiment 1 + 2: I2C stretch latency + execution replay + contract checks
            stretch = run_i2c_stretch_sweep(client, stretch_cycles=7, max_wait_values=range(4, 11))
            self.assertEqual(stretch["boundary"]["status"], "bracketed")
            self.assertEqual(stretch["boundary"]["boundary_low_cycles"], 7)
            passing_delta = [t["measured_release_delta_cycles"] for t in stretch["trials"] if t["passed"]][0]
            self.assertEqual(passing_delta, 11)

            uart_sw = run_uart_period_sweep(client, byte_val=0xA3, bit_cycles_list=(5, 6, 8, 10, 11))
            self.assertIn(8, uart_sw["passing_bit_cycles"])
            self.assertNotIn(5, uart_sw["passing_bit_cycles"])
            self.assertNotIn(11, uart_sw["passing_bit_cycles"])

            transport.configure_target(0, delay_ns=23, spi_resp=0xAA)
            spi_trace = run_spi_boundary_exploration(client, min_low_cycles=2, max_low_cycles=4, method="sweep", delay_ns=23)
            trials_only = [r for r in spi_trace if r.get("type") == "trial"]
            self.assertTrue(all(t["contract_ok"] for t in trials_only))
            exec_rep = execute_replay(spi_trace, client, repeats=4, only_failures=True)
            self.assertTrue(exec_rep["all_deterministic"])
            self.assertEqual(exec_rep["cases"][0]["exact_matches"], 4)

            # 3. SPI Target Responder example compiles cleanly inside 64 words
            self.assertLessEqual(len(build_spi_target_responder_program(0x5A)), 64)

            # 4. Experiment 4 + 5: Differential divergence + sequence & timing minimization
            div3 = eval_ref_vs_dut(transport, client, [0x9F, 0x06, 0x05], gap_wait=2, ref_thresh=1, dut_thresh=6)
            self.assertTrue(div3["diverged"])
            minimized = minimize_divergence(transport, client, [0x9F, 0x06, 0x05], failing_gap=2, max_gap=8)
            self.assertEqual(minimized["minimized_commands"], [0x06, 0x05])
            self.assertEqual(minimized["highest_failing_gap_wait"], 3)
            self.assertEqual(minimized["lowest_passing_gap_wait"], 4)
            rep_min = replay_minimized_case(transport, client, [0x06, 0x05], 3, repeats=3)
            self.assertEqual(rep_min["deterministic_divergence_matches"], 3)

    def test_dynamic_timing_contract_branches_loops_wait_edge_and_negative_cases(self) -> None:
        # 1. Static bounds detect unbounded infinite loops
        unbounded_prog = assemble(["loop_inf:", "NOP", "JMP loop_inf"])
        ub_bounds = analyze_timing_bounds(unbounded_prog, 0)
        self.assertFalse(ub_bounds["bounded"])
        self.assertEqual(ub_bounds["unbounded_pcs"], [0])

        with IcarusSimTransport() as transport:
            transport.reset(1)
            client = ProtocolClient(transport)

            # 2. JZ branches: I2C ACK (1 cycle shorter) vs NACK (1 cycle longer via JMP)
            i2c_words = build_i2c_write_byte_program(0xA0, half_cycles=4, ack_tag=0xAC, nack_tag=0xDE)
            i2c_bounds = analyze_timing_bounds(i2c_words, 0)
            self.assertTrue(i2c_bounds["bounded"])
            self.assertEqual(i2c_bounds["max_cycles"] - i2c_bounds["min_cycles"], 1)

            client.load_program(i2c_words)
            transport.configure_target(2, i2c_ack=1)
            ack_res = client.run_trial(0)
            ack_chk = check_timing_contract(
                i2c_words,
                0,
                observed_cycles=ack_res["cycle_cnt"],
                observed_events=ack_res["events"],
                eng_state=ack_res["eng_state"],
                sample_bits=[0],  # SDA low -> ACK
            )
            self.assertTrue(ack_chk["contract_ok"], ack_chk["errors"])

            transport.configure_target(2, i2c_ack=0)
            nack_res = client.run_trial(0)
            nack_chk = check_timing_contract(
                i2c_words,
                0,
                observed_cycles=nack_res["cycle_cnt"],
                observed_events=nack_res["events"],
                eng_state=nack_res["eng_state"],
                sample_bits=[1],  # SDA high -> NACK
            )
            self.assertTrue(nack_chk["contract_ok"], nack_chk["errors"])
            self.assertEqual(nack_res["cycle_cnt"], ack_res["cycle_cnt"] + 1)

            # Negative: wrong branch outcome or +/-1 expected cycle must FAIL
            wrong_branch = check_timing_contract(
                i2c_words,
                0,
                observed_cycles=ack_res["cycle_cnt"],
                observed_events=ack_res["events"],
                eng_state=ack_res["eng_state"],
                sample_bits=[1],
            )
            self.assertFalse(wrong_branch["contract_ok"])
            off_by_one = check_timing_contract(
                i2c_words,
                0,
                observed_cycles=ack_res["cycle_cnt"] - 1,
                observed_events=ack_res["events"],
                eng_state=ack_res["eng_state"],
                sample_bits=[0],
            )
            self.assertFalse(off_by_one["contract_ok"])

            # 3. SETCOUNT / LOOP across counts including N=0 edge case and nested/multi-iteration
            for count in (0, 1, 5):
                loop_prog = assemble(
                    [
                        f"SETCOUNT 0, {count}",
                        "top_lbl:",
                        "WAIT_PARAM 1",
                        "MARK 0x33, 0",
                        "LOOP 0, top_lbl",
                        "HALT 0",
                    ]
                )
                client.load_program(loop_prog)
                res = client.run_trial(2)
                chk = check_timing_contract(
                    loop_prog,
                    2,
                    observed_cycles=res["cycle_cnt"],
                    observed_events=res["events"],
                    eng_state=res["eng_state"],
                    sample_bits=(),
                    edge_waits=(),
                )
                self.assertTrue(chk["contract_ok"], f"count={count}: {chk['errors']}")
                self.assertEqual(chk["bounds"]["min_cycles"], chk["bounds"]["max_cycles"])

            # Negative: tampered MARK timestamp must FAIL
            bad_mark = check_timing_contract(
                loop_prog,
                2,
                observed_cycles=res["cycle_cnt"],
                observed_events=[{"tag": e["tag"], "ts_cycles": e["ts_cycles"] + 1} for e in res["events"]],
                eng_state=res["eng_state"],
                sample_bits=(),
                edge_waits=(),
            )
            self.assertFalse(bad_mark["contract_ok"])

            # 4. WAIT_EDGE across immediate hit (stretch=0), delayed release (stretch=7), and timeout
            stretch = run_i2c_stretch_sweep(client, stretch_cycles=7, max_wait_values=(4, 7, 10))
            self.assertTrue(all(t["contract_ok"] for t in stretch["trials"]))

            stretch_imm = run_i2c_stretch_sweep(client, stretch_cycles=0, max_wait_values=(0, 3))
            self.assertTrue(all(t["contract_ok"] for t in stretch_imm["trials"]))
            self.assertTrue(all(t["passed"] for t in stretch_imm["trials"]))

            # Negative: claiming timeout outcome on a passed stretch run must FAIL
            probe_prog = build_i2c_stretch_probe_program(scl_pin=0, sda_pin=1, half_cycles=4)
            bad_wait_edge = check_timing_contract(
                probe_prog,
                10,
                observed_cycles=stretch["trials"][2]["cycle_cnt"],
                observed_events=[
                    {"tag": t, "ts_cycles": ts}
                    for t, ts in zip(stretch["trials"][2]["tags"], stretch["trials"][2]["timestamps"])
                ],
                edge_waits=[None],  # Wrong: target released at 7 cycles, did not time out
            )
            self.assertFalse(bad_wait_edge["contract_ok"])

            # Cross-check standalone dynamic predictor on illegal opcode fault path
            fault_prog = [insn("NOP", 0), 0xF000]
            fault_pred = predict_dynamic_contract(fault_prog, 0)
            self.assertEqual(fault_pred["final_state"], "ENG_FAULT")
            self.assertEqual(fault_pred["total_cycles"], 2)

            # Two-sided timing failure window on MODE_SPI_MULTISPEC D3 ([0x06, 0x02, 0x05])
            m_d3 = minimize_divergence(
                transport,
                client,
                [0x06, 0x02, 0x05],
                11,
                max_gap=15,
                ref_thresh=0,
                dut_thresh=1,
                target_mode=5,
                capture_per_cmd=True,
            )
            self.assertEqual(m_d3["local_failing_window"], [10, 15])
            self.assertEqual(m_d3["highest_passing_below_fail"], 9)
            self.assertEqual(m_d3["passing_gaps"], [3, 4, 5, 6, 7, 8, 9])

            # Noisy target classification check (jitter=9 ns across L=3,4,5)
            from tools.noisy_boundary_study import run_repeated_sweep
            from tools.protocol_asm import build_spi_mode0_program
            client.load_program(build_spi_mode0_program(0x55))
            transport.set_seed(0xA11CE001)
            transport.configure_target(6, delay_ns=37, spi_resp=0xAA, i2c_ack=9)
            noisy_sw = run_repeated_sweep(client, low_range=(3, 4, 5), repeats_per_setting=15)
            self.assertEqual(noisy_sw["summary"]["classification"], "probabilistic_transition_region")


if __name__ == "__main__":
    unittest.main()
