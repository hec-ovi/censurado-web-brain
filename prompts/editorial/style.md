# Editorial style guide

The newsroom's qualitative voice, house rules, and lexicon: the WHAT-it-should-read-like
guide the `style` verb prints. It is NOT the enforced numeric bar. The counts the walk
actually enforces live in `cli/workflow/parameters.json` as MIN_SOURCES / MIN_PER_TYPE /
TOPIC_CAP / RESPIN_PASSES, and `step` fills them into the nodes. `set-floor` sets the two
source counts (MIN_SOURCES, MIN_PER_TYPE); the tag cap and the respin budget are edited in
parameters.json directly. Use this file for the voice and the rules; use parameters.json for
the numbers.

## Voice
Escribimos en espanol, con voz propia y desde un punto de vista: cada autor escribe desde el
lado que su persona declara y lo defiende con el dato en la mano. Lo que se mantiene neutral y
exacto son los HECHOS: contamos el hecho antes que la reaccion, atribuimos cada afirmacion a una
fuente nombrada y separamos lo confirmado de lo que una sola parte sostiene. El encuadre, el
enfasis y el argumento son la posicion del autor, no un centro tibio. La nota tiene que atrapar
y sostener la atencion, y gana con el detalle mas filoso y verdadero, nunca con adjetivos huecos.

## Examples
Good: "La autoridad monetaria subio la tasa de referencia al 40 por ciento, segun su comunicado
oficial." (Hecho concreto, cifra, fuente nombrada; el encuadre puede tener postura, el dato no se toca.)

Bad: "En una decision demoledora, volvieron a castigar a los ahorristas." (Adjetivo hueco y
sensacionalista, sin cifra y sin fuente: el filo tiene que venir del dato, no del adjetivo.)

## Rules
Gate (an article must pass these to publish):
- hecho-primero: Abri con el hecho central y su consecuencia concreta.
- atribuir: Nombra la fuente como texto plano ("segun X"), sin enlaces en el cuerpo. Nombra un
  medio solo si es una fuente ASIGNADA del autor; los actores primarios (personas, empresas,
  funcionarios, instituciones y documentos que son la noticia) siempre se nombran.
- fuentes-multiples: Apoya el hecho central en fuentes independientes (el piso lo fija
  MIN_SOURCES en parameters.json).
- confirmado: Distingui lo confirmado de lo que afirma una sola parte.
- sin-inventar: Usa solo datos y citas presentes en las fuentes reunidas.
- neutral: La neutralidad es de los HECHOS (exactos y atribuidos), no de la postura: toma partido en el encuadre y el argumento, nunca deformando el dato.

Preference (aim for these; they sharpen the piece):
- titulo-directo: Escribi un titulo breve y directo: el hecho esencial, sin relleno.
- cifras-con-fuente: Acompana cada cifra con su fuente y su fecha.
- contexto-local: Da el contexto que el lector local necesita, sin asumir que ya lo sabe.
- sin-jerga: Explica cualquier termino tecnico la primera vez que aparece.
- no-repetir: Aporta lo nuevo del dia y enlaza la cobertura previa relacionada.
- cierre-util: Cierra con lo que sigue o lo que aun no se sabe, no con un remate de opinion vacia.
- engancha: Que la nota atrape y entretenga; rompe la monotonia con un recurso donde el material lo permita (una cita destacada, una lista, una cifra suelta, una imagen).

## Lexicon
Banned terms: demoledor, escandaloso, letal, brutal, sin precedentes, increible,
no te lo podes perder.

Preferred swaps: polemico -> discutido, fulmino -> rechazo, castigo -> afecto,
historico -> destacado.

## Sourcing
Apoya el hecho central en al menos MIN_SOURCES fuentes independientes, con al menos
MIN_PER_TYPE de cada tipo (derecha, neutral, izquierda). Si al autor le faltan fuentes de un
tipo, usa la busqueda web a discrecion para INFORMAR el hecho, pero el medio que aparezca ahi es
material de fondo y NO se nombra. Nombra un medio solo si es una fuente ASIGNADA del autor; un dato
sacado de un medio no asignado se atribuye al actor primario (la persona, empresa, funcionario,
institucion o documento que es la noticia) o se cuenta sin nombrar medio. Si el autor no tiene
fuentes asignadas, no nombres ningun medio, solo actores primarios. Atribui cada afirmacion y
nunca inventes citas.

## Structure
- Headline: breve y directo, el hecho esencial, sin relleno ni adjetivos.
- Dateline: CIUDAD, fecha, al inicio del cuerpo.
- Lede: primer parrafo con que paso, quien, cuando y por que importa.
- Tags: hasta TOPIC_CAP etiquetas (parameters.json), nombrando las entidades propias.
- Respin: hasta RESPIN_PASSES pasadas de revision antes de publicar.
