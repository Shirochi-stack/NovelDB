import json
import os

class FontMapper:
    def __init__(self, mapping_path):
        self.mapping = {}
        if mapping_path and os.path.exists(mapping_path):
            try:
                with open(mapping_path, 'r', encoding='utf-8') as f:
                    # The C# code uses JavaScriptSerializer to deserialize Dictionary<string, string>
                    # Python's json.load handles this natively.
                    raw_map = json.load(f)
                    # Convert to char->char map
                    self.mapping = {k: v for k, v in raw_map.items()}
            except Exception as e:
                print(f"Failed to load font mapping: {e}")

    def decode(self, text):
        if not self.mapping:
            return text
        # Translate each character if it exists in the map
        return "".join([self.mapping.get(char, char) for char in text])