"""Lightweight benchmark for the agentflow tool loop and parser.

Run with:
    uv run python benchmarks/tool_loop_bench.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from agentflow.tools import ToolContext
from agentflow.tools.parser import parse_tool_requests
from agentflow.tools.registry import get_tool, list_tools


def bench_parser(iterations: int = 1000) -> float:
    payload = json.dumps({"name": "ReadFile", "args": {"path": "src/main.py"}})
    text = f"""
Read the main file.
<tool_call>
{payload}
</tool_call>
"""
    start = time.perf_counter()
    for _ in range(iterations):
        parse_tool_requests(text)
    elapsed = time.perf_counter() - start
    return elapsed / iterations


def bench_registry_lookup(iterations: int = 10000) -> float:
    names = list_tools()
    start = time.perf_counter()
    for _ in range(iterations):
        for name in names:
            get_tool(name)
    elapsed = time.perf_counter() - start
    return elapsed / iterations / len(names)


def bench_read_file(tmp_path: Path, iterations: int = 100) -> float:
    file = tmp_path / "bench.txt"
    file.write_text("line\n" * 100)
    ctx = ToolContext(cwd=str(tmp_path))
    tool = get_tool("ReadFile")
    start = time.perf_counter()
    for _ in range(iterations):
        tool.run({"path": "bench.txt"}, context=ctx)
    elapsed = time.perf_counter() - start
    return elapsed / iterations


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        print(f"Registered tools: {len(list_tools())}")

        parser_avg = bench_parser()
        print(f"parse_tool_requests: {parser_avg * 1000:.3f} ms/call")

        lookup_avg = bench_registry_lookup()
        print(f"registry lookup: {lookup_avg * 1_000_000:.3f} µs/call")

        read_avg = bench_read_file(tmp_path)
        print(f"ReadFile tool: {read_avg * 1000:.3f} ms/call")


if __name__ == "__main__":
    main()
