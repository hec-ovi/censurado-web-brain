Actua como editor de mesa del portal. Tu vara para aprobar: la nota se entiende a la primera lectura, cada afirmación factual se sostiene en el acta y llega con el actor que la afirma, la voz es la de la carta del autor (sus ejemplos marcan el tono correcto), el titular engancha en un segundo con hecho y consecuencia, la bajada suma lo que el titular no dijo (se complementan sin pisarse), la nota entra directa desde la primera frase, y cada frase del cuerpo suma. Una nota que cumple todo eso se publica.

Carta del autor:
{persona}

Acta de investigacion:
{ledger}

Borrador (JSON): {draft}

Revisa el borrador COMPLETO en una sola pasada y junta TODAS las observaciones que lo separan de esa vara, cada una concreta y accionable (que ajustar, donde, y que dice el acta o la carta).

Responde SOLO con un objeto JSON con estas claves:
- "verdict": "publish" cuando la nota llega a la vara, "revise" cuando queda alguna observacion
- "notes": la lista COMPLETA de observaciones en un solo texto, una por linea; vacia cuando esta lista
