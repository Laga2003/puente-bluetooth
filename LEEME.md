# Puente Bluetooth

El PC captura un texto, el móvil lo envía a `openai/gpt-oss-120b` en Groq y la respuesta vuelve por Bluetooth letra a letra.

## Móvil (Android 7 o superior)

1. Compila el APK con GitHub Actions: sube el contenido de esta carpeta a un repositorio, abre la pestaña **Actions** y descarga `PuenteBluetooth-apk` cuando termine. Instala en el móvil el APK que hay dentro.
2. Crea una clave en https://console.groq.com/keys y pégala en la app.
3. Pulsa **Iniciar servidor** y acepta el permiso de Bluetooth.
4. Deja la app abierta en primer plano. El móvil necesita datos o wifi.

## PC (Windows)

1. Empareja el móvil desde los ajustes de Bluetooth de Windows.
2. Ejecuta `pc\compilar.bat`. Solo este paso necesita Python 3.10 o superior.
3. El programa portable queda en `pc\dist\PuenteBluetooth.exe`.
4. Ábrelo, elige el móvil y pulsa **Conectar**.

## Uso

| Atajo | Acción |
|---|---|
| Ctrl izquierdo + Alt izquierdo + C | Envía el texto seleccionado |
| Ctrl izquierdo + Alt izquierdo + V | Escribe la respuesta donde esté el cursor |
| Ctrl izquierdo + Alt izquierdo + X | Para la escritura; V continúa donde se quedó |

Los atajos y la pausa entre letras se guardan en `config.json`, junto al `.exe`.

## Latencia

Por cada letra, el PC envía una petición y el móvil contesta con esa letra. El valor mostrado es el tiempo de ida y vuelta medido en el PC, sin contar la pausa ni la escritura. No se mide solo la ida porque los relojes del móvil y del PC no están sincronizados.
