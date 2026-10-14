#!/usr/bin/env python3
"""Generuje HTML náhled prezentace z YAML specifikace.

Použití:
    conda run -n fdg-renderer python App/render.py
    conda run -n fdg-renderer python App/render.py --validate
    conda run -n fdg-renderer python App/render.py cesta/k/prezentaci.yaml cesta/k/vystupu.html
    conda run -n fdg-renderer python App/render.py --single

Renderer záměrně nevytváří HTML pro každý slide na serveru. Převádí YAML do
normalizovaných dat vložených do stávajícího klientského rendereru v index.html;
ten zajišťuje navigaci, klávesové zkratky, odhalování odpovědí a iframe grafy.

Volba --single vytvoří jeden samostatný HTML soubor, do kterého se vloží všechny
obrázky (jako data URI) i interaktivní grafy (přes <iframe srcdoc>), takže
prezentace funguje i přenesená na jiný počítač bez složky Assets.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
DEFAULT_INPUT = APP_DIR / "Slides" / "fdg-2026.yaml"
DEFAULT_OUTPUT = APP_DIR / "index.html"
DEFAULT_SINGLE_OUTPUT = APP_DIR / "fdg-2026-single.html"

VENDOR_CHARTJS = APP_DIR / "Assets" / "vendor" / "chart.umd.min.js"
CHARTJS_SCRIPT = re.compile(
    r'<script\s+src="https://cdn\.jsdelivr\.net/npm/chart\.js[^"]*"\s*></script>'
)
MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# Delší strana rastrových obrázků vkládaných do jednosouborové verze (v px).
SINGLE_IMAGE_MAX_EDGE = 1600
SINGLE_JPEG_QUALITY = 80

# Přepíná se v render(); ovlivňuje, zda se assety vkládají jako data URI.
SINGLE_FILE = False
_DATA_URI_CACHE: dict[str, str] = {}

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
    "stat_cards",
    "brand_grid",
    "resource_list",
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


def asset_url(value: str) -> str:
    """Cesta k assetu; v jednosouborovém režimu vloží obsah jako data URI."""
    path = public_path(value)
    if not SINGLE_FILE:
        return path
    if path not in _DATA_URI_CACHE:
        _DATA_URI_CACHE[path] = _data_uri(APP_DIR / path)
    return _DATA_URI_CACHE[path]


def _optimized_image(source: Path) -> tuple[bytes, str] | None:
    """Vytvoří menší JPEG (omezená delší strana, nastavitelná kvalita).

    Vhodné pro fotky i ilustrace na bílém pozadí. Průhlednost PNG se sloučí
    s bílou, proto se výsledek použije jen tehdy, když je menší než originál.
    """
    sips = shutil.which("sips")
    if not sips:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "image.jpg"
            subprocess.run(
                [
                    sips,
                    "-Z",
                    str(SINGLE_IMAGE_MAX_EDGE),
                    "-s",
                    "format",
                    "jpeg",
                    "-s",
                    "formatOptions",
                    str(SINGLE_JPEG_QUALITY),
                    str(source),
                    "--out",
                    str(target),
                ],
                check=True,
                capture_output=True,
            )
            return target.read_bytes(), "image/jpeg"
    except (OSError, subprocess.CalledProcessError):
        return None


def _data_uri(source: Path) -> str:
    if not source.is_file():
        raise RenderError(f"Asset pro vložení neexistuje: {source}")
    suffix = source.suffix.lower()
    mime = MIME_TYPES.get(suffix, "application/octet-stream")
    payload = source.read_bytes()
    if suffix in {".jpg", ".jpeg", ".png"}:
        optimized = _optimized_image(source)
        if optimized and len(optimized[0]) < len(payload):
            payload, mime = optimized
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


def embedded_document(source: Path) -> str:
    """Načte interaktivní HTML pro vložení přes <iframe srcdoc> (jednosouborový režim)."""
    if not source.is_file():
        raise RenderError(f"Vkládané HTML neexistuje: {source}")
    document = source.read_text(encoding="utf-8")
    if CHARTJS_SCRIPT.search(document) and VENDOR_CHARTJS.is_file():
        library = VENDOR_CHARTJS.read_text(encoding="utf-8").replace("</script", "<\\/script")
        document = CHARTJS_SCRIPT.sub(lambda _: f"<script>{library}</script>", document, count=1)
    # V jednosouborové verzi je dokument vždy vložený (nemá vlastní ?embedded=1).
    marker = '<script>document.documentElement.classList.add("embedded");</script>'
    if re.search(r"<head\b[^>]*>", document, re.IGNORECASE):
        return re.sub(
            r"(<head\b[^>]*>)", lambda m: m.group(1) + marker, document, count=1, flags=re.IGNORECASE
        )
    return re.sub(
        r"(<html\b[^>]*>)", lambda m: m.group(1) + marker, document, count=1, flags=re.IGNORECASE
    )


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
    return asset_url(source) if source else None


def poll_option(value: Any, description: str) -> list[str]:
    """Převede možnost hlasování na dvojici [název, citát]."""
    if isinstance(value, dict):
        label = require_text(value.get("label"), f"{description}.label")
        quote = optional_text(value.get("quote")) or ""
        return [label, quote]
    return [require_text(value, description), ""]


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


def validate_source_refs(presentation: dict[str, Any], slides: list[dict[str, Any]]) -> None:
    """Ověří, že odkazy na podklady (source_refs / source_material) existují.

    Cesty jsou relativní ke kořeni projektu. Kontrolují se jen položky, které
    vypadají jako cesta (obsahují "/" nebo končí .md/.markdown), aby nekolidovaly
    případné volné poznámky.
    """
    missing: list[str] = []

    def check(value: Any, where: str) -> None:
        reference = str(value).strip()
        looks_like_path = "/" in reference or reference.lower().endswith((".md", ".markdown"))
        if looks_like_path and not (PROJECT_DIR / reference).exists():
            missing.append(f"{where}: {reference}")

    for reference in as_list(presentation.get("source_material")):
        check(reference, "presentation.source_material")
    for slide in slides:
        for reference in as_list(slide.get("source_refs")):
            check(reference, f"slide {slide['id']}.source_refs")

    if missing:
        raise RenderError("Nalezeny neexistující zdroje:\n- " + "\n- ".join(missing))


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
        duration = slide.get("duration_minutes")
        if duration is not None and (not isinstance(duration, int) or duration < 0):
            raise RenderError(
                f"Slide {identifier}: duration_minutes, pokud je uveden, musí být nezáporné celé číslo."
            )
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
    validate_source_refs(presentation, slides)
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
        result["image"] = asset_url(
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
        result["image"] = asset_url(
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
        cards = []
        for item in items:
            if isinstance(item, dict):
                text = require_text(
                    item.get("label") or item.get("title") or item.get("text"),
                    f"Slide {slide['id']}: položka profile",
                )
                subs = [
                    str(sub)
                    for sub in as_list(item.get("sub") or item.get("items") or item.get("children"))
                    if str(sub).strip()
                ]
                cards.append([text, subs])
            else:
                cards.append([require_text(item, f"Slide {slide['id']}: položka profile"), []])
        result["cards"] = cards
        links = []
        for item in as_list(content.get("links")):
            if isinstance(item, dict):
                url = require_text(
                    item.get("url") or item.get("href"),
                    f"Slide {slide['id']}: link.url",
                )
                label = optional_text(item.get("label")) or url
            else:
                url = label = require_text(item, f"Slide {slide['id']}: link")
            links.append([label, url])
        if links:
            result["links"] = links
        asset = first_image_asset(slide)
        if asset:
            result["image"] = asset_url(
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
        source = require_text(content.get("embed"), f"Slide {slide['id']}: content.embed")
        if SINGLE_FILE:
            result["doc"] = embedded_document(PROJECT_DIR / source)
        else:
            result["src"] = public_path(source)
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
            result["image"] = asset_url(
                require_text(asset.get("src"), f"Slide {slide['id']}: assets[].src")
            )
            alt = optional_text(asset.get("alt"))
            if alt:
                result["alt"] = alt
        return result

    if content_type == "stat_cards":
        result = base_slide(slide, "stats")
        result["items"] = [
            [
                require_text(item.get("value"), f"Slide {slide['id']}: stats[].value"),
                require_text(item.get("label"), f"Slide {slide['id']}: stats[].label"),
            ]
            for item in as_list(content.get("stats"))
        ]
        points = [str(point) for point in as_list(content.get("points"))]
        if points:
            result["points"] = points
        note = optional_text(content.get("note"))
        if note:
            result["note"] = note
        return result

    if content_type == "brand_grid":
        result = base_slide(slide, "brands")
        items = []
        for item in as_list(content.get("items")):
            brand = require_map(item, f"Slide {slide['id']}: položka brand_grid")
            name = require_text(brand.get("name"), f"Slide {slide['id']}: brand.name")
            ticker = optional_text(brand.get("ticker")) or ""
            logo = ""
            logo_source = optional_text(brand.get("logo"))
            if logo_source:
                if logo_source.startswith("App/") and not (PROJECT_DIR / logo_source).is_file():
                    raise RenderError(f"Slide {slide['id']}: logo neexistuje: {logo_source}")
                logo = asset_url(logo_source)
            items.append([name, ticker, logo])
        result["items"] = items
        if content.get("reveal"):
            result["reveal"] = True
        note = optional_text(content.get("note"))
        if note:
            result["note"] = note
        return result

    if content_type == "resource_list":
        result = base_slide(slide, "resources")
        items = []
        for item in as_list(content.get("items")):
            resource = require_map(item, f"Slide {slide['id']}: položka resource_list")
            items.append([
                require_text(resource.get("name"), f"Slide {slide['id']}: resource.name"),
                optional_text(resource.get("tag")) or "",
                optional_text(resource.get("note")) or "",
                optional_text(resource.get("url")) or "",
            ])
        result["items"] = items
        note = optional_text(content.get("note"))
        if note:
            result["note"] = note
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
    output, replacements = DATA_BLOCK.subn(lambda _match: replacement, template, count=1)
    if replacements != 1:
        raise RenderError(
            "V index.html jsem nenašel blok `const sections` + `const slides`. "
            "Obnovte HTML šablonu nebo upravte regulární výraz DATA_BLOCK v render.py."
        )
    return output


def render(
    input_path: Path,
    output_path: Path,
    single: bool = False,
    image_max_edge: int = SINGLE_IMAGE_MAX_EDGE,
    image_quality: int = SINGLE_JPEG_QUALITY,
) -> tuple[int, int]:
    global SINGLE_FILE, SINGLE_IMAGE_MAX_EDGE, SINGLE_JPEG_QUALITY
    SINGLE_FILE = single
    SINGLE_IMAGE_MAX_EDGE = image_max_edge
    SINGLE_JPEG_QUALITY = image_quality
    _DATA_URI_CACHE.clear()

    presentation, yaml_slides = load_presentation(input_path)
    sections = {section["id"]: section["label"] for section in presentation["sections"]}
    slides = [normalized_slide(slide) for slide in yaml_slides]

    try:
        template = DEFAULT_OUTPUT.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RenderError(
            f"Šablona {DEFAULT_OUTPUT} neexistuje. Zachovejte App/index.html jako šablonu rendereru."
        ) from error

    output = replace_generated_data(template, sections, slides)
    output_path.write_text(output, encoding="utf-8")
    return len(slides), presentation["duration_minutes"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vygeneruje FDG HTML z YAML specifikace.")
    parser.add_argument("input", nargs="?", type=Path, default=DEFAULT_INPUT, help="vstupní YAML")
    parser.add_argument("output", nargs="?", type=Path, default=None, help="výstupní HTML")
    parser.add_argument("--validate", action="store_true", help="pouze ověří YAML, bez zápisu HTML")
    parser.add_argument(
        "--single",
        action="store_true",
        help="vytvoří jeden samostatný HTML se vloženými obrázky i interaktivními grafy",
    )
    parser.add_argument(
        "--image-max-edge",
        type=int,
        default=SINGLE_IMAGE_MAX_EDGE,
        help=f"delší strana vkládaných obrázků v px (výchozí {SINGLE_IMAGE_MAX_EDGE})",
    )
    parser.add_argument(
        "--image-quality",
        type=int,
        default=SINGLE_JPEG_QUALITY,
        help=f"kvalita JPEG vkládaných obrázků 0-100 (výchozí {SINGLE_JPEG_QUALITY})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.validate:
            presentation, slides = load_presentation(args.input)
            print(f"YAML je validní: {len(slides)} slidů, {presentation['duration_minutes']} minut.")
        else:
            output = args.output
            if output is None:
                output = DEFAULT_SINGLE_OUTPUT if args.single else DEFAULT_OUTPUT
            count, duration = render(
                args.input,
                output,
                single=args.single,
                image_max_edge=args.image_max_edge,
                image_quality=args.image_quality,
            )
            label = " (jeden soubor)" if args.single else ""
            print(f"Vygenerováno: {output}{label} ({count} slidů, {duration} minut).")
    except RenderError as error:
        print(f"Chyba rendereru: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
