import inspect
import re
import sys
from pathlib import Path

from auth_app.settings import AuthSettings
from chat_app.settings import ChatSettings
from common.settings import CommonSettings

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"

_PROVIDER_RE = re.compile(r'get_secrets_provider\(\)\.get\(\s*"([A-Z0-9_]+)"')


def provider_backed_names(*classes: type) -> set[str]:
    names: set[str] = set()
    for cls in classes:
        names.update(_PROVIDER_RE.findall(inspect.getsource(cls)))
    return names


_SECRET_RE = re.compile(
    r"(_KEY|_PASS|_PASSWORD|_SECRET|_TOKEN)$|^(DATABASE_URL|REDIS_URL|MONGO_URI)$"
)


def expected_env_names() -> set[str]:
    classes = (CommonSettings, AuthSettings, ChatSettings)
    names = {field.upper() for cls in classes for field in cls.model_fields}
    names |= {f"{n}_FILE" for n in provider_backed_names(*classes) if _SECRET_RE.search(n)}
    return names


def documented_env_names(text: str) -> set[str]:
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, re.M))


def main() -> int:
    expected = expected_env_names()
    documented = documented_env_names(ENV_EXAMPLE.read_text())
    missing = sorted(expected - documented)
    if missing:
        print(f".env.example is missing {len(missing)} variable(s):")
        for name in missing:
            print(f"  {name}=")
        return 1
    print(f".env.example documents all {len(expected)} settings variables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
