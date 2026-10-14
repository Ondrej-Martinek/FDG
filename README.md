# FDG 2026 — Finanční a digitální gramotnost

Prezentace pro studenty gymnázií (sexta/septima). Obsah žije v markdownu
(`Content/`, `Notes/`), z něj vzniká YAML specifikace slidů
(`App/Slides/fdg-2026.yaml`) a z té renderer generuje výsledné HTML.

## Rychlý start

```bash
# 1) jednorázově: prostředí s Pythonem + PyYAML
conda env create -f environment.yml          # vytvoří env "fdg-renderer"

# 2) běžná verze (index.html + složka Assets/) – pro vývoj a kontrolu
conda run -n fdg-renderer python App/render.py

# 3) jednosouborová verze (vše vložené, přenosná) – pro prezentování
conda run -n fdg-renderer python App/render.py --single
```

Po každé úpravě YAML spusť **oba** příkazy, ať máš aktuální obě verze.

## Co který příkaz vytvoří

| Příkaz | Výstup | K čemu |
|---|---|---|
| `python App/render.py` | **`App/index.html`** | Běžná verze. Odkazuje relativně na `App/Assets/` – musí se otevírat z té složky. |
| `python App/render.py --single` | **`App/fdg-2026-single.html`** | Jeden soubor se vším uvnitř (obrázky, grafy). Funguje i přenesený bez `Assets/`, klidně offline. |
| `python App/render.py --validate` | nic | Jen zkontroluje YAML, nic nezapíše. |
| `python App/render.py --help` | – | Nápověda. |

Volitelné přepínače pro jednosouborovou verzi (ladění velikosti/kvality):

| Přepínač | Výchozí | Význam |
|---|---|---|
| `--image-max-edge` | `1600` | Delší strana vkládaných obrázků v px. |
| `--image-quality` | `80` | Kvalita JPEG vkládaných obrázků (0–100). |

Vlastní cesta k výstupu (druhý poziční argument):

```bash
conda run -n fdg-renderer python App/render.py App/Slides/fdg-2026.yaml prezentace.html --single
```

## Kde co upravovat

```
App/Slides/fdg-2026.yaml   # ← tady se edituje obsah a pořadí slidů (zdroj pravdy pro HTML)
App/render.py              # renderer + validátor (Python)
App/index.html             # ŠABLONA i výstup běžné verze (viz níže)
App/Assets/                # obrázky, loga a interaktivní HTML grafy
App/Assets/vendor/         # Chart.js (pro offline jednosouborovou verzi)

Content/                   # znalostní báze (témata, otázky, praktické tipy)
Content/medailonek.md      # podklad pro slide "Něco o mě"
Notes/                     # poznámky, grafy, zdroje
```

### Důležité: `App/index.html` je zároveň šablona i výstup

Renderer přepisuje **jen blok** mezi `const sections` a `const slides`
(označený `// GENERATED FROM App/Slides/fdg-2026.yaml`). Zbytek souboru
(CSS, JavaScript, layout) je ruční šablona a při renderu zůstává.

- **Obsah a pořadí slidů** → měň v YAML.
- **Vzhled, CSS, chování** → měň v `App/index.html` (mimo generovaný blok).
- Generovaný blok v `index.html` needituj ručně, přepíše se.

## Struktura YAML

```yaml
presentation:
  duration_minutes: 100        # jen referenční, nevaliduje se
  sections:                    # sekce = skupiny v levé navigaci
    - id: uvod
      label: Úvod
  slides:
    - id: presenter            # unikátní
      order: 20                # unikátní, vzestupné (podle order musí být i pořadí v souboru)
      section: uvod            # musí odpovídat některé z sections.id
      name: "Něco o mě"        # text v navigaci
      title: "Ondřej Martínek" # nadpis slidu
      goal: "..."              # interní poznámka, nezobrazuje se
      duration_minutes: 2      # volitelné, jen referenční
      source_refs: [Content/medailonek.md]
      content:
        type: profile          # viz přehled typů níže
        ...
      assets:
        - type: image
          src: App/Assets/OM face.jpg
      interactions: []
```

### Typy obsahu (`content.type`)

| Kategorie | Typy |
|---|---|
| Úvod / představení | `hero`, `profile`, `narrative` |
| Obrázky | `image`, `image_only`, `quote_with_explanation` |
| Otázky s odhalením | `reveal_question`, `classify_reveal`, `case_study_reveal`, `poll`, `question`, `four_quadrants` |
| Data a přehledy | `stat_cards`, `brand_grid`, `resource_list`, `checklist`, `practical_tips` |
| Závěr | `glossary_index`, `numbered_takeaways`, `sources` |
| Interaktivní graf | `embedded_interactive` (viz níže) |

Plný seznam i přesná pole jsou v `App/render.py` (konstanta `SUPPORTED_TYPES`
a funkce `normalized_slide`).

### Klíčová věta (`key_message`)

Zobrazuje se jako zvýrazněný pruh. U otázek (`classify_reveal`,
`reveal_question`, `case_study_reveal`) se **skryje a ukáže se až s odpovědí**,
aby neprozradila výsledek.

## Interaktivní grafy (`embedded_interactive`)

Interaktivní HTML soubory v `App/Assets/*.html` se vkládají do slidu:

```yaml
content:
  type: embedded_interactive
  embed: App/Assets/financni_paka_interaktivni.html
```

- V běžné verzi se načítají přes `<iframe src="...?embedded=1">`. Parametr
  `?embedded=1` schová jejich vlastní nadpisy a roztáhne je na celou plochu
  slidu.
- V jednosouborové verzi se vkládají přes `<iframe srcdoc="...">` (obsah je
  přímo v HTML) a renderer do nich vloží stejný příznak „embedded“, takže
  fungují i bez složky `Assets/`.
- Nový graf: vytvoř samostatné HTML v `App/Assets/` (jako vzor použij existující
  soubory s režimem `embedded`) a přidej slide výše uvedeným způsobem.

## Obrázky a loga

- Cesty v YAML se píšou od kořene projektu (`App/Assets/...`); renderer je
  převede na cesty relativní k `index.html`.
- V jednosouborové verzi se obrázky vkládají jako data URI a velké rastrové
  obrázky se zmenšují (na macOS přes `sips`). Originály v `Assets/` se nemění.
  Když `sips` není k dispozici, vloží se originály (větší soubor).
- Loga firem (`brand_grid`) jsou jen pro výukovou identifikaci.

## Prezentování

- **Další slide**: `→` nebo `PageDown`; **předchozí**: `←` nebo `PageUp`.
- Tlačítka `←`/`→` dole, přepínání slidů v levé navigaci.
- „Zobrazit odpověď“ odhalí odpověď u otázek; „Odkrýt všechny firmy“ u log.
- Tlačítko „Skrýt navigaci“ schová levý panel (více místa na obsah).
- Jednosouborovou verzi otevři přímo v prohlížeči (dvojklik), funguje offline.

## Prostředí

`environment.yml` definuje conda env `fdg-renderer` (Python 3.12 + PyYAML).
Bez condy stačí mít `python3` s `pyyaml` a volat `python3 App/render.py ...`.

## Poznámky

- `duration_minutes` (celková 100 min i u slidů) je **jen referenční** a
  nevaliduje se.
- `source_refs` ukazují na podklady v `Content/` a `Notes/`; renderer jejich
  existenci kontroluje (cesty jsou relativní ke kořeni projektu). Položky
  označené `requires_fact_check` je před publikací vhodné ověřit.
  - Pozor na čárky v názvech souborů v YAML — v hranatých závorkách je nutné
    je uzavřít do uvozovek, např. `source_refs: ["Notes/Pojmy, grafy, otázky.md"]`.
- Více o pipeline viz `App/architecture.md`, `App/storyboard.md`,
  `App/style.md`.
