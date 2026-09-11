"""Launch real WeChat staged human acceptance; never starts sending on launch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_bot.acceptance_panel import main  # noqa: E402

if __name__ == "__main__":
    main()
