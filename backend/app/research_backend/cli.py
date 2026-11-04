"""Local development utilities. No public endpoint issues identity tokens."""
import argparse
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from jose import jwt
from .config import Settings, load_environment


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    token = sub.add_parser("token", help="Generate a local development token using the configured secret")
    token.add_argument("--subject", required=True)
    token.add_argument("--minutes", type=int, default=60)
    schema = sub.add_parser("openapi", help="Export API contracts without starting databases or external providers")
    schema.add_argument("--out", type=Path, required=True)
    init = sub.add_parser("init", help="Create a local .env.research with a random JWT secret; never overwrite")
    init.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[2] / ".env.research")
    args = parser.parse_args()
    if args.command == "init":
        template = (Path(__file__).resolve().parents[2] / ".env.research.example").read_text("utf-8")
        template = template.replace("JWT_SECRET_KEY=\n", "JWT_SECRET_KEY=" + secrets.token_urlsafe(48) + "\n")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(template)
        print("Created local configuration; fill RESEARCH_LLM_API_KEY in that file. No credentials printed.")
        return
    load_environment()
    settings = Settings.from_env()
    if args.command == "token":
        if len(settings.jwt_secret) < 32 or settings.jwt_secret.startswith("your-"):
            parser.error("Set JWT_SECRET_KEY to a non-example secret of at least 32 characters")
        if not args.subject.strip() or len(args.subject) > 200 or not 1 <= args.minutes <= 1440:
            parser.error("Invalid subject or lifetime (1..1440 minutes)")
        print(jwt.encode({"sub": args.subject, "exp": datetime.now(timezone.utc) + timedelta(minutes=args.minutes)},
                         settings.jwt_secret, algorithm=settings.jwt_algorithm))
    else:
        from .api import create_app
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(create_app(settings).openapi(), ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
