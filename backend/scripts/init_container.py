"""Create the local demo configuration once; keep its identity across restarts."""
from pathlib import Path
import secrets
import sys


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).resolve().parents[1] / ".env.research.example"
    content = template.read_text(encoding="utf-8").replace(
        "JWT_SECRET_KEY=\n", "JWT_SECRET_KEY=" + secrets.token_urlsafe(48) + "\n")
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(content)
        path.chmod(0o600)
        print("Created persistent local demo configuration. No credentials printed.")
    except FileExistsError:
        print("Using existing local demo configuration.")


if __name__ == "__main__":
    initialize(Path(sys.argv[1]))
