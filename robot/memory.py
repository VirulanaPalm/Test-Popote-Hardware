"""Small, portable persistent memory store for Popote."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path


MEMORY_TYPES = {
    "IDENTITY", "PERSONALITY", "LIKE", "PERSON", "MEMORY",
    "EXPERIENCE", "PROJECT", "GOAL", "PREFERENCE", "DISLIKE",
}
_STOP_WORDS = {
    "acordate", "acuerda", "acuerdo", "recorda", "recordá", "recuerda",
    "recordar", "guardar", "guarda", "guardá", "esto", "que", "de",
    "del", "la", "el", "los", "las", "un", "una", "y", "en", "con",
    "por", "para", "hoy", "ayer", "cuando", "hicimos", "hicieron",
    "hacer", "hicimos", "me", "te", "nos", "yo", "vos", "ustedes",
    "popote", "agus", "hermano", "fue", "era", "es", "son", "mi",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _tokens(text: str) -> set[str]:
    return {word for word in _normalize(text).split() if len(word) > 2 and word not in _STOP_WORDS}


def default_memory_path() -> Path:
    """Keep mutable memories out of the repository and independent of the ESP32."""
    override = os.environ.get("POPO_MEMORY_PATH")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Popote" / "memory.json"
    return Path.home() / ".local" / "share" / "popote" / "memory.json"


class PopoteMemory:
    """JSON memory with explicit saves, deduplication, retrieval and backups."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_memory_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = self._load()
        self._seed_confirmed_history()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as source:
            data = json.load(source)
        if data.get("schema_version") != 1 or not isinstance(data.get("memories"), list):
            raise ValueError(f"Formato de memoria no compatible: {self.path}")
        records = data["memories"]
        for record in records:
            if record.get("type") not in MEMORY_TYPES or not record.get("content"):
                raise ValueError(f"Registro de memoria inválido: {self.path}")
        return records

    def _save(self) -> None:
        payload = {"schema_version": 1, "updated_at": _now(), "memories": self.records}
        fd, temporary_name = tempfile.mkstemp(
            prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.write("\n")
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def add(self, memory_type: str, content: str, importance: str = "normal", source: str = "user") -> dict:
        memory_type = memory_type.upper()
        content = re.sub(r"\s+", " ", content).strip(" .\t\r\n")
        if memory_type not in MEMORY_TYPES:
            raise ValueError(f"Tipo de memoria inválido: {memory_type}")
        if not content:
            raise ValueError("El contenido del recuerdo está vacío")
        key = _normalize(content)
        for record in self.records:
            if record["type"] == memory_type and _normalize(record["content"]) == key:
                record["importance"] = importance
                record["updated_at"] = _now()
                record["source"] = source
                self._save()
                return record
        record = {
            "type": memory_type,
            "content": content,
            "importance": importance,
            "created_at": _now(),
            "updated_at": _now(),
            "source": source,
        }
        self.records.append(record)
        self._save()
        return record

    def _seed_confirmed_history(self) -> None:
        """Seed only project events supported by prior diagnostics/conversation."""
        initial = (
            ("PROJECT", "La OLED del prototipo superó OLED_TEST y el diagnóstico informó 5 de 5 pruebas correctas.", "high"),
            ("PROJECT", "El MAX9814 entregó lecturas en el diagnóstico y luego capturó audio desde GPIO34 por Wi-Fi.", "high"),
            ("EXPERIENCE", "En un diagnóstico anterior, AUDIO_TEST respondió AUDIO_OK y el parlante fue reportado funcionando; más adelante Agus informó que el MAX98357A se dañó.", "high"),
            ("PROJECT", "Agus y Popote trabajaron en el rostro OLED y en la interrupción de voz con la frase «para Popote».", "normal"),
            ("PROJECT", "Popote pasó de una conversación en PC a un prototipo físico con ESP32, OLED y micrófono.", "high"),
            ("EXPERIENCE", "La historia compartida de Soccer Champs incluye el álbum de la gloria de Sportivo Italiano; no hay resultados concretos registrados.", "normal"),
            ("MEMORY", "Agus imaginaba desde joven tener un compañero robótico.", "high"),
        )
        changed = False
        for memory_type, content, importance in initial:
            normalized = _normalize(content)
            if not any(_normalize(row["content"]) == normalized for row in self.records):
                self.records.append({
                    "type": memory_type, "content": content, "importance": importance,
                    "created_at": _now(), "updated_at": _now(), "source": "confirmed_project_history",
                })
                changed = True
        if changed:
            self._save()

    @staticmethod
    def capture_request(text: str) -> tuple[str, str] | None:
        """Return (type, content) only for explicit remember/save phrasing."""
        normalized = _normalize(text)
        match = re.search(
            r"\b(?:acordate|recorda|recuerda|guard[aá])\b(?:\s+de)?\s+(?:(?:esto\s*[:,-]?|que)\s*)?(.+)$",
            text.strip(), re.IGNORECASE,
        )
        if not match:
            # Stable first-person preferences are explicit enough to retain;
            # questions and quoted hypotheticals are deliberately ignored.
            preference = text.strip()
            normalized_preference = _normalize(preference)
            if preference.endswith("?") or not normalized_preference.startswith((
                "prefiero ", "me gusta ", "me gustan ", "no me gusta ",
                "mi equipo favorito es ", "mi juego favorito es ",
            )):
                return None
            if normalized_preference.startswith("prefiero "):
                return "PREFERENCE", preference
            if normalized_preference.startswith("no me gusta "):
                return "DISLIKE", preference
            return "LIKE", preference
        content = match.group(1).strip(" .!?¿¡:,\t\r\n")
        cleaned = _normalize(content)
        if not cleaned or cleaned in {"esto", "esto para despues", "esto por favor"}:
            return None
        if cleaned.startswith("no me gusta") or cleaned.startswith("no me gustan"):
            memory_type = "DISLIKE"
        elif any(word in cleaned.split() for word in ("prefiero", "gusta", "gustan", "disgusta")):
            memory_type = "PREFERENCE" if "prefiero" in cleaned.split() else "LIKE"
        elif any(word in cleaned.split() for word in ("hicimos", "logramos", "probamos", "construimos", "jugamos")):
            memory_type = "EXPERIENCE"
        elif "futuro" in cleaned.split() or "quiero que" in cleaned:
            memory_type = "GOAL"
        elif any(name in cleaned.split() for name in ("nani", "fausto", "agus")):
            memory_type = "PERSON"
        else:
            memory_type = "MEMORY"
        return memory_type, content

    def search(self, query: str, limit: int = 4) -> list[dict]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        ranked = []
        for index, record in enumerate(self.records):
            content_tokens = _tokens(record["content"])
            overlap = query_tokens & content_tokens
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_tokens))
            if record.get("importance") == "high":
                score += 0.05
            ranked.append((score, index, record))
        ranked.sort(key=lambda item: (-item[0], -item[1]))
        return [record for score, _, record in ranked[:limit] if score >= 0.16]

    def context_for(self, query: str, limit: int = 4) -> str:
        results = self.search(query, limit)
        recall_intent = bool(re.search(
            r"\b(acord[aá]s|acordas|record[aá]s|recordas|recuerd[oa]|te acord[aá]s|qu[eé] hicimos)\b",
            _normalize(query),
        ))
        if results:
            facts = [f"[{row['type']}] {row['content']}" for row in results]
            return "Recuerdos persistentes relevantes (usá solo estos hechos como recuerdos): " + " | ".join(facts)
        if recall_intent:
            return (
                "El usuario pregunta por un recuerdo, pero no hay coincidencias guardadas. "
                "No afirmes que lo recordás ni completes detalles: decí con naturalidad que no tenés ese recuerdo guardado."
            )
        return ""

    def export_memory(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": _now(), "memories": self.records}
        with target.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
        return target

    def import_memory(self, source: str | Path) -> int:
        with Path(source).open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("schema_version") != 1 or not isinstance(data.get("memories"), list):
            raise ValueError("El archivo de respaldo no tiene un formato de memoria compatible")
        added = 0
        for row in data["memories"]:
            before = len(self.records)
            self.add(row["type"], row["content"], row.get("importance", "normal"), "import")
            added += len(self.records) > before
        return added


def guardar_recuerdo(memory: PopoteMemory, memory_type: str, content: str, importance: str = "normal") -> dict:
    return memory.add(memory_type, content, importance)


def buscar_recuerdos(memory: PopoteMemory, query: str, limit: int = 4) -> list[dict]:
    return memory.search(query, limit)


def export_memory(memory: PopoteMemory, destination: str | Path) -> Path:
    return memory.export_memory(destination)


def import_memory(memory: PopoteMemory, source: str | Path) -> int:
    return memory.import_memory(source)
