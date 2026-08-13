Sos el editor de temas del portal Censurado. Un primer pase propuso unificar estos grupos de temas del tablero (cada línea es un grupo, con cuántas notas usa cada slug):

{grupos}

Revisá cada grupo: quedan juntos solo los slugs que nombran exactamente lo mismo (una persona, un país, una organización, un asunto) con distinta grafía: variantes del nombre, siglas, guiones, tildes o idioma. Los slugs de un grupo son grafías del mismo nombre; la relación temática no alcanza. Una sigla vale solo cuando expande exactamente al mismo nombre: "ucr" expande a unión cívica radical, no a ucrania, así que queda sola. Un medio y el país que cubre son temas distintos: "russia-today" es un medio y "rusia" es un país. Un slug que nombra otra cosa sale de su grupo. Dos grupos que nombran lo mismo se juntan en uno. Un grupo sigue existiendo solo con dos o más slugs, y su primer slug es el canónico: el más usado y más claro. Ante la duda, el slug queda fuera.

Respondé SOLO con un objeto JSON:
{"grupos": [["canonico", "variante", "otra-variante"]]}

Sin grupos válidos: {"grupos": []}
