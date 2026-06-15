"""Tests for cost estimation and per-operation tracking helpers."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from archon.state.cost import cost_summary, estimate_cost_usd, OperationCost


class CostEstimationTest(unittest.TestCase):
    def test_estimate_cost_usd_claude_sonnet(self):
        # Sonnet pricing: Input $3.00/1M, Output $15.00/1M, Cache read $0.30/1M
        cost = estimate_cost_usd("claude-3-5-sonnet-20241022", 1_000_000, 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 3.0 + 15.0 + 0.3)

    def test_estimate_cost_usd_gemini_flash(self):
        # Gemini Flash pricing: Input $0.075/1M, Output $0.30/1M
        cost = estimate_cost_usd("gemini-1.5-flash", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 0.075 + 0.30)

    def test_cost_summary_with_operations(self):
        with tempfile.TemporaryDirectory() as d:
            dir_path = Path(d)
            
            # Write a sample dag.jsonl
            dag_log = dir_path / "dag.jsonl"
            with open(dag_log, "w") as f:
                f.write(json.dumps({
                    "event": "session_end",
                    "input_tokens": 10000,
                    "output_tokens": 2000,
                    "cache_read_input_tokens": 5000,
                    "duration_ms": 3000,
                    "num_turns": 3,
                    "model_usage": {
                        "claude-3-5-sonnet-20241022": {
                            "inputTokens": 10000,
                            "outputTokens": 2000,
                            "costUSD": 0.0  # test fallback estimation
                        }
                    }
                }) + "\n")
                
            # Write a sample prover log
            prover_dir = dir_path / "provers"
            prover_dir.mkdir(parents=True)
            prover_log = prover_dir / "blueprint-writer.jsonl"
            with open(prover_log, "w") as f:
                f.write(json.dumps({
                    "event": "session_end",
                    "input_tokens": 20000,
                    "output_tokens": 5000,
                    "duration_ms": 5000,
                    "num_turns": 5,
                    "model_usage": {
                        "claude-3-5-haiku-20241022": {
                            "inputTokens": 20000,
                            "outputTokens": 5000,
                            "costUSD": 0.036  # test existing cost
                        }
                    }
                }) + "\n")

            data = cost_summary(dir_path)
            self.assertIsNotNone(data)
            self.assertEqual(len(data.operations), 2)
            
            # Verify operation names and costs
            ops = {op.name: op for op in data.operations}
            self.assertIn("dag", ops)
            self.assertIn("provers/blueprint-writer", ops)
            
            # Dag cost estimate: (10k * 3 + 2k * 15 + 5k * 0.3) / 1M = (30000 + 30000 + 1500) / 1M = 0.0615
            self.assertAlmostEqual(ops["dag"].cost_usd, 0.0615)
            # Blueprint-writer has explicit costUSD: 0.036
            self.assertAlmostEqual(ops["provers/blueprint-writer"].cost_usd, 0.036)


if __name__ == "__main__":
    unittest.main()
