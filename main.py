"""Popote Hardware serial handshake and five-part hardware diagnostic."""

from hardware.connection import ESP32Connection
from hardware.controller import (
    DiagnosticResult,
    DiagnosticStatus,
    HardwareController,
)
from protocol.protocol import EXPECTED_FIRMWARE_VERSION


DIAGNOSTIC_NAMES = (
    "PING/PONG",
    "Estado del hardware",
    "OLED",
    "MAX9814",
    "MAX98357A + Parlante",
)


def print_header():
    print()
    print("=" * 50)
    print(f"       POPOTE HARDWARE v{EXPECTED_FIRMWARE_VERSION}")
    print("        HARDWARE DIAGNOSTIC")
    print("=" * 50)
    print()


def _print_result(result: DiagnosticResult):
    if result.status is DiagnosticStatus.OK:
        print(f"[OK]   {result.name}")
    elif result.status is DiagnosticStatus.NOT_AVAILABLE:
        print(f"[N/A]  {result.name}")
    else:
        detail = f" - {result.detail}" if result.detail else ""
        print(f"[{result.status.value}] {result.name}{detail}")


def _record_setup_failure(results, error):
    """Count connection/handshake failure once; unrun component tests are N/A."""
    completed = {result.name for result in results}
    if "PING/PONG" not in completed:
        results.append(
            DiagnosticResult("PING/PONG", DiagnosticStatus.ERROR, str(error))
        )
    for name in DIAGNOSTIC_NAMES[1:]:
        if name not in completed:
            results.append(
                DiagnosticResult(
                    name,
                    DiagnosticStatus.NOT_AVAILABLE,
                    "No se pudo establecer la conexión serial",
                )
            )


def main():
    print_header()
    connection = ESP32Connection()
    results = []

    try:
        print("[USB] Buscando ESP32...")
        connection.connect()
        print(f"[OK] Puerto serial detectado: {connection.port}")
        controller = HardwareController(connection)

        print("\n" + "-" * 50)
        print("HANDSHAKE")
        print("-" * 50)
        handshake = controller.test_connection()
        results.append(handshake)

        if handshake:
            info = controller.query_device_info()
            print("\n[INFO] Identificación del dispositivo")
            for key in ("device", "firmware", "protocol", "capabilities"):
                if info.get(key):
                    print(f"[INFO] {info[key]}")
                else:
                    print(f"[WARNING] {key}: no anunciado por este firmware")
            firmware = info.get("firmware")
            if firmware:
                expected_reply = f"FIRMWARE_VERSION:{EXPECTED_FIRMWARE_VERSION}"
                if firmware == expected_reply:
                    print(f"[OK] Firmware esperado v{EXPECTED_FIRMWARE_VERSION}")
                else:
                    print(
                        f"[WARNING] Se esperaba firmware v{EXPECTED_FIRMWARE_VERSION}: "
                        f"{firmware}"
                    )

            print("\n" + "-" * 50)
            print("DIAGNÓSTICO")
            print("-" * 50)
            results.extend(
                (
                    controller.test_status(),
                    controller.test_oled(),
                    controller.test_microphone(),
                    controller.test_audio(),
                )
            )
        else:
            # Hardware tests are not attempted when the protocol handshake fails.
            for name in DIAGNOSTIC_NAMES[1:]:
                results.append(
                    DiagnosticResult(
                        name,
                        DiagnosticStatus.NOT_AVAILABLE,
                        detail="Sin handshake con el ESP32",
                    )
                )

    except Exception as error:
        print(f"\n[ERROR] {error}")
        _record_setup_failure(results, error)
    finally:
        connection.disconnect()

    print("\n" + "=" * 50)
    print("              RESULTADO FINAL")
    print("=" * 50)

    for result in results:
        _print_result(result)

    passed = sum(result.status is DiagnosticStatus.OK for result in results)
    failed = sum(
        result.status in (DiagnosticStatus.FAIL, DiagnosticStatus.TIMEOUT,
                          DiagnosticStatus.ERROR)
        for result in results
    )
    unavailable = sum(
        result.status is DiagnosticStatus.NOT_AVAILABLE for result in results
    )
    print(f"\nPruebas correctas : {passed}")
    print(f"Pruebas fallidas  : {failed}")
    print(f"No disponibles   : {unavailable}")

    if passed == len(DIAGNOSTIC_NAMES) and failed == 0:
        print("\n" + "=" * 50)
        print("       HARDWARE DIAGNOSTIC: PASSED")
        print("=" * 50)
    else:
        print("\n" + "=" * 50)
        print("       HARDWARE DIAGNOSTIC: FAILED")
        print("=" * 50)
    print()


if __name__ == "__main__":
    main()
