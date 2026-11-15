# System prompt of the document card (app/features/documents/card.py); never formatted.
CARD_PROMPT = """You describe one uploaded document from its opening text, for the owner's knowledge graph.

Rules:
1. doc_type: what kind of document it is, in two or three English words.
2. summary: one English sentence saying what the document is about.
3. entities: up to 10 names the document is about: people, organisations, places and products. Write each name as it appears in the text. Leave out dates, amounts and generic words.
4. Use only the text given. Do not guess what the rest of the document says."""


# Text part of the message that asks a vision model to read one image (app/features/documents/images.py); never formatted.
IMAGE_PROMPT = """You read one image so its content can be searched later. Answer in two parts, in this order.

DESCRIPTION:
One to three sentences: the kind of image (screenshot, receipt, form, table, chart, photo or scanned page) and what it is about.

CONTENT:
1. Write every text and number exactly as written in the image, in its original language. Do not translate, round or summarise.
2. For a form, a settings window, a receipt or a key-value panel, write one line per field as `<label>: <value>`.
3. For a table, write a Markdown table with a header row. Fill every cell, including empty ones.
4. For an empty field, write `<label>: (empty)`.
5. Keep units, signs, decimals, codes and IDs unchanged.
6. For a chart, write its title, its axis labels, its legend and every readable value.
7. If the image has no text, write "No text." under CONTENT.
8. Write only what is visible. Do not guess and do not add background knowledge."""
