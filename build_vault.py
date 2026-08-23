import os
import glob
import json
import sqlite3
import sqlite_vec
import struct
from fastembed import TextEmbedding

# 1. Load embedding model
print("Loading embedding model...")
model = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")

# 2. Connect to SQLite and load vector extension
db = sqlite3.connect("vault.db")
db.enable_load_extension(True)
sqlite_vec.load(db)
db.enable_load_extension(False)

# 3. Create Database Tables
db.execute("DROP TABLE IF EXISTS vault_embeddings")
db.execute("CREATE VIRTUAL TABLE vault_embeddings USING vec0(embedding float[384])")

db.execute("DROP TABLE IF EXISTS vault_text")
db.execute("CREATE TABLE vault_text(rowid INTEGER PRIMARY KEY, rule_name TEXT, rule_data TEXT)")

def robust_read_json(file_path):
    """Safely loads standard JSON, concatenated JSON objects, or JSON lines."""
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read().strip()

    if not content:
        return []

    # Try standard parse first
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return [data]
    except json.JSONDecodeError:
        pass

    # Fallback: Parse multiple concatenated objects / lines
    results = []
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(content):
        # Skip leading whitespace
        while idx < len(content) and content[idx].isspace():
            idx += 1
        if idx >= len(content):
            break
        try:
            obj, end_idx = decoder.raw_decode(content, idx)
            results.append(obj)
            idx = end_idx
        except json.JSONDecodeError:
            idx += 1  # Skip bad char and continue
    return results

# 4. Gather documents
documents = []

# Load Lazy_GM.json
if os.path.exists("Lazy_GM.json"):
    items = robust_read_json("Lazy_GM.json")
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            for k, v in item.items():
                val_str = json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                documents.append((f"Lazy GM: {k}", val_str))
        else:
            documents.append((f"Lazy GM Entry {idx}", str(item)))

# Load all 5e_srd JSON files
srd_files = glob.glob("5e_srd/*.json")
for file_path in srd_files:
    category = os.path.basename(file_path).replace(".json", "")
    items = robust_read_json(file_path)
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            title = item.get("name") or item.get("title") or f"{category}_{idx}"
            documents.append((f"{category}: {title}", json.dumps(item)))
        else:
            documents.append((f"{category}_{idx}", str(item)))

print(f"Collected {len(documents)} rule entries to embed.")

# 5. Generate embeddings and insert into DB
if documents:
    texts_to_embed = [f"{name}: {content}" for name, content in documents]
    embeddings = list(model.embed(texts_to_embed))

    for rowid, ((name, content), emb) in enumerate(zip(documents, embeddings), start=1):
        emb_bytes = struct.pack(f"{len(emb)}f", *emb)
        db.execute("INSERT INTO vault_text(rowid, rule_name, rule_data) VALUES (?, ?, ?)", (rowid, name, content))
        db.execute("INSERT INTO vault_embeddings(rowid, embedding) VALUES (?, ?)", (rowid, emb_bytes))

    db.commit()
    print("Vault database successfully built into vault.db!")
else:
    print("No valid documents found to embed.")

db.close()
