"""Launch two-contact automatic replies; stays stopped until the user starts Bot."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_bot.acceptance_panel import main  # noqa: E402

if __name__ == "__main__":
    main()
