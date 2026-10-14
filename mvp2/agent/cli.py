"""python -m mvp2.agent.cli <request, case, or cases JSON file>."""

import argparse
import json
import sys
from pathlib import Path

from .rule_engine import evaluate


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"유효하지 않은 JSON 숫자: {value}")


def main(argv=None) -> int:
    # Both successful JSON and errors must survive redirected Windows streams.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_json", type=Path)
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.case_json.read_text(encoding="utf-8-sig"),
                          object_pairs_hook=_strict_pairs, parse_constant=_reject_constant)
    except (OSError, ValueError) as error:
        parser.exit(2, f"사례 JSON을 읽을 수 없습니다: {error}\n")
    if isinstance(data, dict) and "cases" in data:
        if not isinstance(data["cases"], list) or not all(isinstance(case, dict) and "input" in case for case in data["cases"]):
            parser.exit(2, "cases는 input을 가진 사례 객체 배열이어야 합니다.\n")
        results = [{"case_id": case.get("case_id"), "response": evaluate(case["input"])} for case in data["cases"]]
        output = {"results": results}
        failed = any(item["response"]["run_status"] == "오류" for item in results)
    else:
        if isinstance(data, dict) and "call_B_evaluate" in data:
            data = data["call_B_evaluate"]
        if isinstance(data, dict):
            data = data.get("input", data.get("request", data))
        output = evaluate(data)
        failed = output["run_status"] == "오류"
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
