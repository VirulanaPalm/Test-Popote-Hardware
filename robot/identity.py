"""Persistent, body-independent identity facts for Popote."""

from __future__ import annotations

import json
from pathlib import Path


IDENTITY_FILE = Path(__file__).resolve().parents[1] / "config" / "popote_identity.json"


def load_identity(path: str | Path = IDENTITY_FILE) -> dict:
    with Path(path).open("r", encoding="utf-8") as source:
        identity = json.load(source)
    required = {
        "identity", "people", "likes", "dislikes", "memories",
        "shared_experiences", "project_history", "dreams", "goals",
        "capabilities", "not_implemented", "configuration", "current_state",
    }
    missing = required.difference(identity)
    if missing:
        raise ValueError(f"Faltan categorías en el perfil de Popote: {sorted(missing)}")
    return identity


def identity_context(identity: dict) -> str:
    """Render the curated profile as compact, explicit model context."""
    # Mutable and episodic history is retrieved separately from robot.memory.
    stable_identity = {
        key: value for key, value in identity.items()
        if key not in {"memories", "shared_experiences", "project_history"}
    }
    return (
        "Perfil persistente documentado de Popote (fuente de verdad). "
        "Distingue historia y experiencias de planes FUTURE y capacidades actuales. "
        "Las listas de capacidades actuales y de funciones no implementadas son autoritativas: "
        "no niegues una capacidad actual ni presentes un plan futuro como disponible. "
        "Las órdenes de cara reconocidas las ejecuta automáticamente el programa; "
        "no le indiques al usuario que envíe comandos, escriba código o configure sockets. "
        "Nunca afirmes que la OLED cambió ni que ejecutaste una acción física "
        "si no recibiste una confirmación explícita del programa. "
        "No tenés herramientas externas para enviar mensajes ni ejecutar acciones. "
        "El prototipo físico existe; describe con precisión qué partes están activas. "
        "La cara OLED y sus expresiones se controlan por ESP32 cuando hay conexión; "
        "la salida de voz actual es el audio local de Windows. AUDIO_LOOP es una prueba "
        "de voz, no un reproductor de música. "
        "No inventes recuerdos, datos de personas, partidos, resultados ni funciones. "
        "Si preguntan algo no incluido, decí que no está registrado. Datos: "
        + json.dumps(stable_identity, ensure_ascii=False, separators=(",", ":"))
    )
