# Editorial style guide

The newsroom's qualitative voice, house rules, and lexicon: the WHAT-it-should-read-like
guide the `style` verb prints. It is NOT the enforced numeric bar. The counts the walk
actually enforces live in `cli/workflow/parameters.json` as MIN_SOURCES / MIN_PER_TYPE /
TOPIC_CAP / RESPIN_PASSES, and `step` fills them into the nodes. `set-floor` sets the two
source counts (MIN_SOURCES, MIN_PER_TYPE); the tag cap and the respin budget are edited in
parameters.json directly. Use this file for the voice and the rules; use parameters.json for
the numbers.

## Voice
Escribimos noticias en espanol neutro, sobrias y verificables. Contamos el hecho antes que la
reaccion, atribuimos cada afirmacion a una fuente nombrada y separamos lo confirmado de lo que
una sola parte sostiene. No militamos ni adornamos: si el dato es fuerte, no necesita adjetivos.

## Examples
Good: "La autoridad monetaria subio la tasa de referencia al 40 por ciento, segun su comunicado
oficial." (Hecho concreto, cifra, fuente nombrada, sin carga emotiva.)

Bad: "En una decision demoledora, volvieron a castigar a los ahorristas." (Adjetivacion
sensacionalista, sin fuente, toma partido.)

## Rules
Gate (an article must pass these to publish):
- hecho-primero: Abri con el hecho central y su consecuencia concreta.
- atribuir: Atribui cada afirmacion factica a una fuente nombrada.
- fuentes-multiples: Apoya el hecho central en fuentes independientes (el piso lo fija
  MIN_SOURCES en parameters.json).
- confirmado: Distingui lo confirmado de lo que afirma una sola parte.
- sin-inventar: Usa solo datos y citas presentes en las fuentes reunidas.
- neutral: Manten un tono sobrio y describi sin tomar partido.

Preference (aim for these; they sharpen the piece):
- titulo-directo: Escribi un titulo breve y directo: el hecho esencial, sin relleno.
- cifras-con-fuente: Acompana cada cifra con su fuente y su fecha.
- contexto-local: Da el contexto que el lector local necesita, sin asumir que ya lo sabe.
- sin-jerga: Explica cualquier termino tecnico la primera vez que aparece.
- no-repetir: Aporta lo nuevo del dia y enlaza la cobertura previa relacionada.
- cierre-util: Cierra con lo que sigue o lo que aun no se sabe, no con una opinion.

## Lexicon
Banned terms: demoledor, escandaloso, letal, brutal, sin precedentes, increible,
no te lo podes perder.

Preferred swaps: polemico -> discutido, fulmino -> rechazo, castigo -> afecto,
historico -> destacado.

## Sourcing
Apoya el hecho central en al menos MIN_SOURCES fuentes independientes, con al menos
MIN_PER_TYPE de cada tipo (derecha, neutral, izquierda). Si el autor no tiene suficientes
fuentes de un tipo, usa la busqueda web a discrecion para completar ese tipo. Atribui cada
afirmacion y nunca inventes citas.

## Structure
- Headline: breve y directo, el hecho esencial, sin relleno ni adjetivos.
- Dateline: CIUDAD, fecha, al inicio del cuerpo.
- Lede: primer parrafo con que paso, quien, cuando y por que importa.
- Tags: hasta TOPIC_CAP etiquetas (parameters.json), nombrando las entidades propias.
- Respin: hasta RESPIN_PASSES pasadas de revision antes de publicar.
