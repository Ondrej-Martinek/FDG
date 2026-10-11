#!/usr/bin/env python3
"""Generuje HTML náhled prezentace z YAML specifikace.

Použití:
    conda run -n fdg-renderer python App/render.py
    conda run -n fdg-renderer python App/render.py --validate
    conda run -n fdg-renderer python App/render.py cesta/k/prezentaci.yaml cesta/k/vystupu.html

Renderer záměrně nevytváří HTML pro každý slide na serveru. Převádí YAML do
normalizovaných dat vložených do stávajícího klientského rendereru v index.html;
ten zajišťuje navigaci, klávesové zkratky, odhalování odpovědí a iframe grafy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
DEFAULT_INPUT = APP_DIR / "Slides" / "fdg-2026.yaml"
DEFAULT_OUTPUT = APP_DIR / "index.html"

SUPPORTED_TYPES = {
    "hero",
    "image",
    "image_only",
    "profile",
    "poll",
    "question",
    "reveal_question",
    "quote_with_explanation",
    "embedded_interactive",
    "four_quadrants",
    "classify_reveal",
    "case_study_reveal",
    "narrative",
    "checklist",
    "practical_tips",
    "glossary_index",
    "numbered_takeaways",
    "sources",
}

DATA_BLOCK = re.compile(
    r"(?ms)^    (?:\/\/ GENERATED FROM .*?\n)?"
    r"    const sections = .*?^    const slides = .*?;\n"
)


class RenderError(Exception):
    """Chyba, kterou je vhodné ukázat autorovi YAML."""


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RenderError(f"Očekávám seznam, ale dostal jsem: {value!r}")
    return value


def require_map(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderError(f"{description} musí být objekt/mapa.")
    return value


def require_text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RenderError(f"{description} musí být neprázdný text.")
    return value.strip()


def optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def public_path(value: str) -> str:
    """Převede cestu v YAML z kořene projektu na cestu relativní k App/index.html."""
    path = value.replace("\\", "/")
    return path.removeprefix("App/")


def first_image_asset(slide: dict[str, Any]) -> dict[str, Any] | None:
    for asset in as_list(slide.get("assets")):
        if not isinstance(asset, dict) or asset.get("type") != "image":
            continue
        if optional_text(asset.get("src")):
            return asset
    return None


def first_image(slide: dict[str, Any]) -> str | None:
    asset = first_image_asset(slide)
    source = optional_text(asset.get("src")) if asset else None
    return public_path(source) if source else None


def poll_option(value: Any, description: str) -> list[str]:
    """Převede možnost hlasování na dvojici [název, citát]."""
    if isinstance(value, dict):
        label = require_text(value.get("label"), f"{description}.label")
        quote = optional_text(value.get("quote")) or ""
        return [label, quote]
    return [require_text(value, description), ""]


def speaker_notes(slide: dict[str, Any]) -> list[str]:
    return [str(note) for note in as_list(slide.get("speaker_notes")) if str(note).strip()]


def key_message(content: dict[str, Any]) -> str | None:
    return optional_text(content.get("key_message"))


def interaction_options(slide: dict[str, Any]) -> list[str]:
    for interaction in as_list(slide.get("interactions")):
        if isinstance(interaction, dict) and isinstance(interaction.get("options"), list):
            return [str(option) for option in interaction["options"]]
    return []


def validate_asset_paths(slides: list[dict[str, Any]]) -> None:
    missing: list[str] = []
    for slide in slides:
        for asset in as_list(slide.get("assets")):
            if not isinstance(asset, dict):
                continue
            source = optional_text(asset.get("src"))
            if source and source.startswith("App/") and not (PROJECT_DIR / source).is_file():
                missing.append(f"{slide['id']}: {source}")

        content = require_map(slide.get("content"), f"Slide {slide['id']}: content")
        embedded = optional_text(content.get("embed"))
        if embedded and embedded.startswith("App/") and not (PROJECT_DIR / embedded).is_file():
            missing.append(f"{slide['id']}: {embedded}")

    if missing:
        raise RenderError("Nalezeny neexistující assety:\n- " + "\n- ".join(missing))


def validate(presentation: dict[str, Any]) -> list[dict[str, Any]]:
    require_text(presentation.get("id"), "presentation.id")
    require_text(presentation.get("title"), "presentation.title")

    sections = as_list(presentation.get("sections"))
    section_ids = []
    for section in sections:
        section_map = require_map(section, "Položka sections")
        section_ids.append(require_text(section_map.get("id"), "sections[].id"))
        require_text(section_map.get("label"), "sections[].label")
    if len(section_ids) != len(set(section_ids)):
        raise RenderError("ID sekcí se nesmí opakovat.")

    slides = [require_map(item, "Položka slides") for item in as_list(presentation.get("slides"))]
    if not slides:
        raise RenderError("presentation.slides nesmí být prázdné.")

    ids: list[str] = []
    orders: list[int] = []
    for position, slide in enumerate(slides, start=1):
        identifier = require_text(slide.get("id"), f"Slide #{position}: id")
        ids.append(identifier)
        require_text(slide.get("name"), f"Slide {identifier}: name")
        require_text(slide.get("title"), f"Slide {identifier}: title")
        if slide.get("section") not in section_ids:
            raise RenderError(f"Slide {identifier}: neznámá sekce {slide.get('section')!r}.")
        if not isinstance(slide.get("order"), int):
            raise RenderError(f"Slide {identifier}: order musí být celé číslo.")
        orders.append(slide["order"])
        if not isinstance(slide.get("duration_minutes"), int) or slide["duration_minutes"] < 0:
            raise RenderError(f"Slide {identifier}: duration_minutes musí být nezáporné celé číslo.")
        content = require_map(slide.get("content"), f"Slide {identifier}: content")
        content_type = require_text(content.get("type"), f"Slide {identifier}: content.type")
        if content_type not in SUPPORTED_TYPES:
            raise RenderError(
                f"Slide {identifier}: nepodporovaný content.type {content_type!r}. "
                f"Podporované: {', '.join(sorted(SUPPORTED_TYPES))}."
            )

    if len(ids) != len(set(ids)):
        duplicates = sorted({identifier for identifier in ids if ids.count(identifier) > 1})
        raise RenderError("ID slidů se nesmí opakovat: " + ", ".join(duplicates))
    if orders != sorted(orders):
        raise RenderError("Slidy musí být v YAML seřazeny vzestupně podle order.")
    if len(orders) != len(set(orders)):
        raise RenderError("Hodnoty order se nesmí opakovat.")

    validate_asset_paths(slides)
    return slides


def base_slide(slide: dict[str, Any], kind: str) -> dict[str, Any]:
    content = require_map(slide["content"], f"Slide {slide['id']}: content")
    result: dict[str, Any] = {
        "s": slide["section"],
        "k": kind,
        "n": slide["name"],
        "t": slide["title"],
    }
    message = key_message(content)
    if message:
        result["m"] = message
    notes = speaker_notes(slide)
    if notes:
        result["notes"] = notes
    return result


def normalized_slide(slide: dict[str, Any]) -> dict[str, Any]:
    content = require_map(slide["content"], f"Slide {slide['id']}: content")
    content_type = content["type"]

    if content_type == "hero":
        result = base_slide(slide, "hero")
        result["sub"] = content.get("subtitle", "")
        return result

    if content_type == "image":
        asset = first_image_asset(slide)
        if not asset:
            raise RenderError(
                f"Slide {slide['id']}: content.type 'image' vyžaduje v assets obrázek typu image."
            )
        result = base_slide(slide, "image")
        result["image"] = public_path(
            require_text(asset.get("src"), f"Slide {slide['id']}: assets[].src")
        )
        alt = optional_text(asset.get("alt"))
        if alt:
            result["alt"] = alt
        caption = optional_text(content.get("caption"))
        if caption:
            result["caption"] = caption
        return result

    if content_type == "image_only":
        asset = first_image_asset(slide)
        if not asset:
            raise RenderError(
                f"Slide {slide['id']}: content.type 'image_only' vyžaduje v assets obrázek typu image."
            )
        result = base_slide(slide, "image_only")
        result["image"] = public_path(
            require_text(asset.get("src"), f"Slide {slide['id']}: assets[].src")
        )
        alt = optional_text(asset.get("alt"))
        if alt:
            result["alt"] = alt
        return result

    if content_type == "profile":
        result = base_slide(slide, "profile")
        role = optional_text(content.get("role") or content.get("subtitle"))
        if role:
            result["sub"] = role
        items = as_list(content.get("bullets") or content.get("options"))
        result["cards"] = [
            require_text(item, f"Slide {slide['id']}: položka profile") for item in items
        ]
        asset = first_image_asset(slide)
        if asset:
            result["image"] = public_path(
                require_text(asset.get("src"), f"Slide {slide['id']}: assets[].src")
            )
            alt = optional_text(asset.get("alt"))
            if alt:
                result["alt"] = alt
        return result

    if content_type == "poll":
        result = base_slide(slide, "poll")
        result["items"] = [
            poll_option(item, f"Slide {slide['id']}: content.options")
            for item in as_list(content.get("options"))
        ]
        return result

    if content_type == "question":
        result = base_slide(slide, "question")
        result["sub"] = content.get("subtitle", "")
        result["cards"] = interaction_options(slide)
        return result

    if content_type == "reveal_question":
        reveal = require_map(content.get("reveal"), f"Slide {slide['id']}: content.reveal")
        result = base_slide(slide, "reveal")
        result.update({
            "q": require_text(content.get("question"), f"Slide {slide['id']}: content.question"),
            "a": require_text(reveal.get("answer"), f"Slide {slide['id']}: reveal.answer"),
            "e": require_text(reveal.get("explanation"), f"Slide {slide['id']}: reveal.explanation"),
        })
        return result

    if content_type == "quote_with_explanation":
        result = base_slide(slide, "quote")
        result.update({
            "quote": require_text(content.get("quote"), f"Slide {slide['id']}: content.quote"),
            "by": content.get("attribution", ""),
        })
        image = first_image(slide)
        if image:
            result["image"] = image
        return result

    if content_type == "embedded_interactive":
        result = base_slide(slide, "embed")
        result["src"] = public_path(require_text(content.get("embed"), f"Slide {slide['id']}: content.embed"))
        return result

    if content_type == "four_quadrants":
        result = base_slide(slide, "quadrants")
        items = []
        for item in as_list(content.get("items")):
            quadrant = require_map(item, f"Slide {slide['id']}: položka quadrants")
            items.append([
                require_text(quadrant.get("label"), "quadrant.label"),
                require_text(quadrant.get("definition"), "quadrant.definition"),
                require_text(quadrant.get("prompt"), "quadrant.prompt"),
                require_text(quadrant.get("color"), "quadrant.color"),
            ])
        result["items"] = items
        return result

    if content_type == "classify_reveal":
        reveal = require_map(content.get("reveal"), f"Slide {slide['id']}: content.reveal")
        result = base_slide(slide, "classify")
        result.update({
            "q": require_text(content.get("scenario"), f"Slide {slide['id']}: content.scenario"),
            "a": require_text(reveal.get("category"), f"Slide {slide['id']}: reveal.category"),
            "e": require_text(reveal.get("explanation"), f"Slide {slide['id']}: reveal.explanation"),
            "color": require_text(reveal.get("color"), f"Slide {slide['id']}: reveal.color"),
        })
        return result

    if content_type == "case_study_reveal":
        reveal = require_map(content.get("reveal"), f"Slide {slide['id']}: content.reveal")
        result = base_slide(slide, "case")
        result.update({
            "q": require_text(content.get("prompt"), f"Slide {slide['id']}: content.prompt"),
            "cards": [str(reason) for reason in as_list(reveal.get("reasons"))],
            "caveat": optional_text(reveal.get("caveat")) or "",
        })
        asset = first_image_asset(slide)
        if asset:
            result["image"] = public_path(
                require_text(asset.get("src"), f"Slide {slide['id']}: assets[].src")
            )
            alt = optional_text(asset.get("alt"))
            if alt:
                result["alt"] = alt
        return result

    if content_type == "narrative":
        result = base_slide(slide, "story")
        result["items"] = [str(beat) for beat in as_list(content.get("beats"))]
        return result

    if content_type in {"checklist", "practical_tips"}:
        result = base_slide(slide, "checklist")
        result["items"] = [str(item) for item in as_list(content.get("items"))]
        return result

    if content_type == "glossary_index":
        result = base_slide(slide, "glossary")
        result["items"] = [str(item) for item in as_list(content.get("terms"))]
        return result

    if content_type == "numbered_takeaways":
        result = base_slide(slide, "takeaways")
        result["items"] = [str(item) for item in as_list(content.get("items"))]
        return result

    if content_type == "sources":
        result = base_slide(slide, "sources")
        result["items"] = [str(item) for item in as_list(content.get("items"))]
        result["m"] = content.get("disclaimer", result.get("m", ""))
        return result

    raise AssertionError(f"Validovaný typ se nepřevedl: {content_type}")


def load_presentation(input_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        document = yaml.safe_load(input_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RenderError(f"Vstupní YAML neexistuje: {input_path}") from error
    except yaml.YAMLError as error:
        raise RenderError(f"YAML nelze načíst: {error}") from error

    document = require_map(document, "Kořen YAML")
    presentation = require_map(document.get("presentation"), "presentation")
    slides = validate(presentation)
    return presentation, slides


def replace_generated_data(template: str, sections: dict[str, str], slides: list[dict[str, Any]]) -> str:
    sections_json = json.dumps(sections, ensure_ascii=False, indent=6)
    slides_json = json.dumps(slides, ensure_ascii=False, indent=6)
    # Ochrana proti nechtěnému ukončení scriptu, pokud by se takový text objevil v obsahu slidu.
    sections_json = sections_json.replace("</", "<\\/")
    slides_json = slides_json.replace("</", "<\\/")
    replacement = (
        "    // GENERATED FROM App/Slides/fdg-2026.yaml — upravujte YAML, ne tento blok.\n"
        f"    const sections = {sections_json};\n"
        f"    const slides = {slides_json};\n"
    )
    output, replacements = DATA_BLOCK.subn(replacement, template, count=1)
    if replacements != 1:
        raise RenderError(
            "V index.html jsem nenašel blok `const sections` + `const slides`. "
            "Obnovte HTML šablonu nebo upravte regulární výraz DATA_BLOCK v render.py."
        )
    return output


def render(input_path: Path, output_path: Path) -> tuple[int, int]:
    presentation, yaml_slides = load_presentation(input_path)
    sections = {section["id"]: section["label"] for section in presentation["sections"]}
    slides = [normalized_slide(slide) for slide in yaml_slides]

    try:
        template = output_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RenderError(
            f"Výstupní HTML/šablona neexistuje: {output_path}. "
            "Zachovejte App/index.html jako šablonu rendereru."
        ) from error

    output = replace_generated_data(template, sections, slides)
    output_path.write_text(output, encoding="utf-8")
    return len(slides), presentation["duration_minutes"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vygeneruje FDG HTML z YAML specifikace.")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT, help="vstupní YAML")
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT, help="výstupní HTML")
    parser.add_argument("--validate", action="store_true", help="pouze ověří YAML, bez zápisu HTML")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.validate:
            presentation, slides = load_presentation(args.input)
            print(f"YAML je validní: {len(slides)} slidů, {presentation['duration_minutes']} minut.")
        else:
            count, duration = render(args.input, args.output)
            print(f"Vygenerováno: {args.output} ({count} slidů, {duration} minut).")
    except RenderError as error:
        print(f"Chyba rendereru: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
