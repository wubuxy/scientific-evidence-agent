"""Bounded subprocess entry point; only server-created JSON is read."""
import json
import sys
import traceback
from pathlib import Path


def main():
    directory = Path(sys.argv[1])
    def write(name, data):
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
        if name == "progress.json":
            # Windows readers may deny deletion of an open file. Progress is a
            # disposable snapshot, so tolerate partial reads instead of renaming.
            try:
                (directory / name).write_text(encoded, encoding="utf-8")
            except PermissionError:
                pass
            return
        temporary = directory / (name + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(directory / name)
    try:
        from .analysis import compute_analysis
        payload = json.loads((directory / "input.json").read_text(encoding="utf-8"))
        result = compute_analysis(payload["data"], payload["parameters"], progress=lambda value: write("progress.json", value), artifact_dir=directory)
        write("output.json", dict(result=result))
    except ValueError as exc:
        write("output.json", dict(error=str(exc)))
    except Exception:
        traceback.print_exc()
        write("output.json", dict(error="数值分析执行失败，请检查服务端日志和分析依赖"))


if __name__ == "__main__":
    main()
