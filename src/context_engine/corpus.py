"""把三类知识源统一成可检索文档：MDL 语义模型、术语表、golden SQL。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import DATA_DIR, WREN_UI_URL


@dataclass
class Doc:
    id: str
    type: str               # model | column | metric | view | enum | glossary | golden_sql
    title: str
    text: str               # 参与检索的全部文本（含中文同义词）
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_mdl() -> dict[str, Any]:
    """从运行中的 Wren 配置服务拉取已部署 MDL。"""
    resp = httpx.get(f"{WREN_UI_URL}/mdl", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _model_docs(mdl: dict[str, Any]) -> list[Doc]:
    docs: list[Doc] = []
    for model in mdl.get("models", []):
        mname = model["name"]
        ref = model.get("tableReference", {})
        cols = model.get("columns", [])
        col_lines = [f"{c['name']}({c.get('type','?')})={c.get('expression', c['name'])}" for c in cols]
        docs.append(Doc(
            id=f"model:{mname}",
            type="model",
            title=mname,
            text=f"模型 {mname} 表 {ref.get('table','')} 字段: " + " ".join(col_lines),
            payload={"name": mname, "tableReference": ref,
                     "columns": [{"name": c["name"], "type": c.get("type"),
                                  "expression": c.get("expression")} for c in cols],
                     "primaryKey": model.get("primaryKey")},
        ))
        for col in cols:
            docs.append(Doc(
                id=f"column:{mname}.{col['name']}",
                type="column",
                title=f"{mname}.{col['name']}",
                text=f"字段 {col['name']} 属于模型 {mname} 物理列 {col.get('expression','')} 类型 {col.get('type','')}",
                payload={"model": mname, **col},
            ))
    return docs


def _metric_view_enum_docs(mdl: dict[str, Any]) -> list[Doc]:
    docs: list[Doc] = []
    for metric in mdl.get("metrics", []):
        measures = ", ".join(f"{m['name']}={m.get('expression','')}" for m in metric.get("measure", []))
        dims = ", ".join(d["name"] for d in metric.get("dimension", []))
        docs.append(Doc(
            id=f"metric:{metric['name']}", type="metric", title=metric["name"],
            text=f"指标 {metric['name']} 基于 {metric.get('baseObject','')} 度量 {measures} 维度 {dims}",
            payload=metric,
        ))
    for view in mdl.get("views", []):
        docs.append(Doc(
            id=f"view:{view['name']}", type="view", title=view["name"],
            text=f"视图 {view['name']} {view.get('properties',{}).get('description','')} {view.get('statement','')}",
            payload=view,
        ))
    for enum in mdl.get("enumDefinitions", []):
        values = ", ".join(f"{v['name']}={v.get('properties',{}).get('label','')}" for v in enum.get("values", []))
        docs.append(Doc(
            id=f"enum:{enum['name']}", type="enum", title=enum["name"],
            text=f"枚举 {enum['name']} 取值 {values}", payload=enum,
        ))
    return docs


def _glossary_docs() -> list[Doc]:
    path = Path(DATA_DIR) / "glossary.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    docs = []
    for item in data.get("terms", []):
        syn = " ".join(item.get("synonyms", []))
        docs.append(Doc(
            id=f"glossary:{item['term']}", type="glossary", title=item["term"],
            text=f"{item['term']} 同义词 {syn} {item.get('meaning','')}",
            payload=item,
        ))
    return docs


def _golden_docs() -> list[Doc]:
    path = Path(DATA_DIR) / "golden_sql.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    docs = []
    for i, item in enumerate(data.get("queries", [])):
        tags = " ".join(item.get("tags", []))
        docs.append(Doc(
            id=f"golden_sql:{i}", type="golden_sql", title=item["question"],
            text=f"{item['question']} 标签 {tags} {item.get('sql','')}",
            payload=item,
        ))
    return docs


def build_corpus(mdl: dict[str, Any] | None = None) -> tuple[list[Doc], dict[str, Any]]:
    """返回（文档列表, MDL 原文）；MDL 缺省时实时从 Wren 拉取。"""
    mdl = mdl or fetch_mdl()
    docs = (_model_docs(mdl) + _metric_view_enum_docs(mdl)
            + _glossary_docs() + _golden_docs())
    return docs, mdl
