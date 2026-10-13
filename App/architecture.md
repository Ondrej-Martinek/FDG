# Architektura

1. Zdrojové materiály - markdown
- Fakta, příklady, otázky, paradoxy, zdroje, poznámky
- "knowledge base"

2. Prezentace - storyboard
- Výběr témat, pořadí, cíle, návrh slidů, návrh vizualizací

3. Specifikace prezentace - YAML (píše AI, člověk jen pro kontrolu)
- Validovatelná definice jednotlivých slidů

4. Finální prezentace - HTML + CSS + JavaScript
- Renderer, grafy, animace, interaktivní prvky a prezentační režim
- Jednosouborová verze (`python App/render.py --single`) vloží obrázky i interaktivní grafy přímo do jednoho HTML, takže funguje i přenesená bez složky Assets

