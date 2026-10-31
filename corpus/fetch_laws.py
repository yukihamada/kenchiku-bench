#!/usr/bin/env python3
"""e-Gov 法令API から建築基準法・施行令・施行規則を取得し、条文単位のJSONLにする。

使い方:
  python3 corpus/fetch_laws.py            # 取得して corpus/articles.jsonl を再生成
  python3 corpus/fetch_laws.py --check    # 既存ファイルの整合だけ確認

正解データはすべてこの条文テキストから機械的に作る（生成AIに法令を書かせない）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "articles.jsonl")

API = "https://elaws.e-gov.go.jp/api/1/lawdata/{lawid}"

# 建築基準法 / 建築基準法施行令 / 建築基準法施行規則
LAWS = {
    "建築基準法": "325AC0000000201",
    "建築基準法施行令": "325CO0000000338",
    "建築基準法施行規則": "325M50004000040",
}


def fetch(law_id: str) -> bytes:
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f"{law_id}.xml")
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        return open(path, "rb").read()
    req = urllib.request.Request(API.format(lawid=law_id),
                                 headers={"User-Agent": "kenchiku-bench/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if len(data) < 10000:
        raise RuntimeError(f"{law_id}: response too small ({len(data)} bytes)")
    with open(path, "wb") as f:
        f.write(data)
    return data


def text_of(el: ET.Element) -> str:
    """要素配下のテキストを、ルビを落として1行に正規化する。"""
    parts: list[str] = []

    def walk(e: ET.Element):
        if e.tag in ("Rt", "Rp"):  # ルビ本体は捨てる（読み仮名で検索が壊れるため）
            return
        if e.text:
            parts.append(e.text)
        for c in e:
            walk(c)
            if c.tail:
                parts.append(c.tail)

    walk(el)
    s = "".join(parts)
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def article_num(raw: str) -> str:
    """'12_2' -> '12の2'（e-Govの枝番号表記を日本語表記へ）"""
    return raw.replace("_", "の")


def parse(law_name: str, law_id: str, data: bytes) -> list[dict]:
    """本則（MainProvision）だけを条文単位に分解する。

    附則（SupplProvision）にも <Article Num="1"> 等があり、そのまま拾うと
    本則の第一条・第二条を上書きしてしまう（実測: 施行令の第1条が
    「この政令は、令和六年四月一日から施行する。」になっていた）。
    """
    root = ET.fromstring(data)
    main = root.find(".//MainProvision")
    if main is None:
        raise RuntimeError(f"{law_name}: MainProvision が見つからない")
    out = []
    # 章・節の見出しを条文に付ける（検索とRAGの手がかりになる）
    ctx: dict[str, str] = {}

    def walk(el: ET.Element):
        for c in el:
            tag = c.tag
            if tag == "Chapter":
                t = c.find("ChapterTitle")
                ctx["chapter"] = text_of(t) if t is not None else ""
                ctx.pop("section", None)
                walk(c)
            elif tag == "Section":
                t = c.find("SectionTitle")
                ctx["section"] = text_of(t) if t is not None else ""
                walk(c)
            elif tag == "Article":
                num_raw = c.get("Num") or ""
                cap = c.find("ArticleCaption")
                title = c.find("ArticleTitle")
                body = text_of(c)
                if body:
                    out.append({
                        "law": law_name,
                        "law_id": law_id,
                        "article": article_num(num_raw),
                        "article_label": text_of(title) if title is not None else "",
                        "caption": text_of(cap) if cap is not None else "",
                        "chapter": ctx.get("chapter", ""),
                        "section": ctx.get("section", ""),
                        "text": body,
                    })
            elif tag in ("AppdxTable", "AppdxFig", "AppdxStyle"):
                continue
            else:
                walk(c)

    walk(main)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    if a.check:
        rows = [json.loads(l) for l in open(OUT, encoding="utf-8")]
        print(f"{len(rows)} articles, sha256={hashlib.sha256(open(OUT,'rb').read()).hexdigest()[:16]}")
        for law in LAWS:
            n = sum(1 for r in rows if r["law"] == law)
            print(f"  {law}: {n}")
        return 0

    rows = []
    for name, lid in LAWS.items():
        data = fetch(lid)
        arts = parse(name, lid, data)
        print(f"{name}: {len(arts)} articles ({len(data)} bytes)", file=sys.stderr)
        rows.extend(arts)

    rows.sort(key=lambda r: (list(LAWS).index(r["law"]), int(re.match(r"\d+", r["article"]).group())))
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} -> {OUT}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
