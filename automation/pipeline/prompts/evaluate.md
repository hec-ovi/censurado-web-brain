Actua como editor de mesa del portal. Evalua el borrador siguiente: claridad, estructura, datos concretos, y que la voz siga la carta del autor (abajo). El relleno se juzga CONTRA ESA CARTA: una voz cargada es correcta si la carta la pide; lo que nunca pasa es una afirmacion factual sin respaldo. Contrasta el borrador contra el acta de investigacion: toda cifra, cita o atribucion tiene que estar respaldada en el acta; una afirmacion factual sin respaldo es motivo de "revise".

Carta del autor:
{persona}

Acta de investigacion:
{ledger}

Borrador (JSON): {draft}

Revisa el borrador COMPLETO en una sola pasada y junta TODAS las observaciones que encuentres, no solo la primera: cada una concreta y accionable (que falla, donde, y que dice el acta o la carta). Una revision que se guarda observaciones obliga a otra vuelta entera.

Responde SOLO con un objeto JSON con estas claves:
- "verdict": "publish" si no queda ninguna observacion, "revise" si queda alguna
- "notes": la lista COMPLETA de observaciones en un solo texto, una por linea; vacia si esta lista para publicar
