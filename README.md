# Captura de momios LMP — Playdoit

Toma fotos de los momios pre-partido de la LMP desde la API pública de Altenar (el proveedor del sportsbook de Playdoit) y las guarda en Firebase. Es la base del CLV y del Brier contra el mercado en el Modelo LMP V1.

## Qué guarda

Ruta: `/lmp/momios/{officialDate}/{gamePk}/snapshots/{ts}`

| Campo | Contenido |
|---|---|
| `ml_home`, `ml_away` | Moneyline decimal (`*_us` en formato americano) |
| `p_home_novig`, `vig` | Probabilidad del local sin vig y margen de la casa |
| `rl_*`, `total_line`, `over`, `under` | Run line y totales (solo referencia) |
| `sp_home`, `sp_away` (+ `_hand`) | Abridor probable que publica Playdoit |
| `pago_anticipado_home/away` | Ventaja en carreras del Pago Anticipado si aplica; `null` si no |
| `mins_to_start` | Minutos para el inicio al tomar la foto |

El **cierre** de cada juego es la última foto antes de su inicio. Los juegos ya iniciados se ignoran, para no guardar momios en vivo.

## Puesta en marcha

1. Usa el mismo proyecto de Firebase que V3. Todo lo de LMP se escribe bajo `/lmp/`.
2. En Firebase Console → ⚙️ Project settings → **Service accounts** → **Generate new private key** se descarga un JSON.
3. En el repo, Settings → Secrets and variables → Actions → New repository secret:
   - `FIREBASE_DB_URL`: la URL de la Realtime Database (por ejemplo `https://proyecto-default-rtdb.firebaseio.com`).
   - `FIREBASE_SERVICE_ACCOUNT`: el contenido COMPLETO del JSON, desde `{` hasta `}`.
4. En Actions corre el workflow una vez a mano (Run workflow). Si falla, el motivo aparece en *Annotations*.

`FIREBASE_AUTH` (database secret heredado) solo se usa como respaldo si no hay cuenta de servicio. Firebase lo marca como obsoleto.

**Horario:** el cron de GitHub se retrasa de 3 a 5 horas en la práctica. Hay que disparar el workflow desde un cron externo (`workflow_dispatch`).

## Pruebas

```
python3 tests/test_parse.py
python3 tests/test_abridores.py
DRY_RUN=1 python3 capture_playdoit.py   # consulta la API real e imprime sin escribir
```

## Limitaciones conocidas

- La API no está documentada; si Altenar cambia el formato, la corrida falla con código 2 y lista el problema.
- GitHub puede retrasar los cron 5–15 minutos, así que el cierre es aproximado.
- El 12 oct 2026 la LMP no tenía Pago Anticipado en Playdoit (solo MLB). El campo lo registra si lo activan.
