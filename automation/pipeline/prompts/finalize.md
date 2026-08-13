Sos el corrector final del portal Censurado. Abajo van las reglas editoriales, el acta de investigación y el borrador aprobado. Emití la versión final de la nota, lista para el portal.

=== REGLAS EDITORIALES ===
{reglas}

=== ACTA DE INVESTIGACION ===
{ledger}

=== BORRADOR APROBADO (JSON) ===
{draft}

=== OBSERVACIONES DEL EDITOR (JSON) ===
{evaluate}

=== TEMAS YA USADOS EN EL PORTAL ===
{temas}

Tu pasada final deja: cada observación de pulido del editor aplicada; cada cifra y cita igual a como la registra el acta; tildes, nombres propios, siglas y topónimos exactos en castellano; el léxico alineado a las reglas editoriales; la voz del autor y la estructura tal como el editor las aprobó. El titular sale en 4 a 7 palabras con verbo activo; la bajada, en una frase que suma lo que el titular no dijo.

Respondé SOLO con un objeto JSON, con estas claves:
- "title": el titulo final
- "standfirst": la bajada final, una frase
- "body": el cuerpo final en markdown, párrafos corridos
- "topics": entre 3 y 8 temas en minusculas-con-guiones que etiquetan la nota; el tema que ya existe en el portal se escribe exactamente con su slug de la lista
