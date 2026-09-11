"""Read one text and image sample in the named test chat. No model calls or sends."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from wechat_bot.adapters.native_review import NativeReview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    backend = NativeReview(project)
    try:
        matches = [c for c in backend.candidates() if c["window_title"] == args.title]
        if len(matches) != 1:
            raise ValueError("Test chat missing or ambiguous")
        backend.prepare_connect(matches)
        backend.connect()
        chat = matches[0]
        rows = [r for r in backend.read_rows(chat) if r["sender"] != backend.self_id]
        samples = []
        for kind in (1, 3):
            matching = [r for r in rows if r["kind"] == kind]
            if matching:
                packet = backend.packet(chat, matching[-1])
                samples.append(asdict(packet))
        output = project / "data/acceptance/read-preflight.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"samples": [{"kind": s["kind"], "text_length": len(s["text"]),
                                        "image_decoded": bool(s["image"]), "note": s["note"]}
                                       for s in samples],
                          "human_a_pass": False, "model_called": False, "sent": False},
                         ensure_ascii=False))
    finally:
        backend.close()


if __name__ == "__main__":
    main()
