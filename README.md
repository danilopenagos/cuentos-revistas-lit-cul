# Cuentos en revistas

Base de datos consultable de cuentos hispanoamericanos y españoles publicados en revistas literarias y culturales.
Navegación por **país → revista → número → cuento**, fichas de autor y búsqueda facetada
(autor, revista, país de la revista, país del autor, sexo, rúbrica, idioma, extensión, año).

Sitio estático: sin servidor ni base de datos en producción. Todo se genera a partir de los datos de los rpyectos "Digitalization and Analysis of Cultural Transfers in Colombian cultural and Literary Magazines" and .

## Estructura

```
data/
  cuentos.csv      fuente principal: una fila por publicación (cabeceras originales)
  revistas.csv     una fila por revista: ciudad, país, años, repositorio digital, QID de Wikidata
  autores.csv      una fila por autor: QID de Wikidata (sin implementar), VIAF (sin implementar)
build.py           CSV → site/data/db.json + site/data/cuentos.sqlite + reports/validacion.md
site/index.html    interfaz (HTML + JS sin dependencias)
.github/workflows/deploy.yml   construye y publica en GitHub Pages en cada push a main
```

## Uso local

```bash
python build.py              # genera los datos y el informe de validación
python build.py --strict     # falla si hay avisos (útil antes de una release)
cd site && python -m http.server 8000   # abrir http://localhost:8000
```

Solo usa la biblioteca estándar de Python (3.10+).

## Publicar

1. Crear el repositorio en GitHub y subir este contenido a `main`.
2. *Settings → Pages → Build and deployment → Source: GitHub Actions*.
3. Cada push regenera los datos y publica. El informe de validación aparece en el resumen de la ejecución.

## Ficha de revistas (`data/revistas.csv`)

| Columna | Contenido | Ejemplo |
|---|---|---|
| revista | Título exacto, igual que en `cuentos.csv` | Athenea |
| ciudad | Ciudad de publicación | |
| pais | País de publicación (nombre actual) | Colombia |
| inicio, fin | Años de publicación | 1927 |
| repositorio | Nombre del repositorio de la digitalización | Biblioteca Digital … |
| url_digital | Enlace a la revista digitalizada (`https://…`) | |
| wikidata_qid | Identificador de Wikidata, solo `Q…` | Q12345 |
| notas | Libre (no se publica) | |

Cada vez que se ejecuta el build, las revistas que aún no tienen ficha se listan en
`reports/revistas_pendientes.csv` con estas columnas, listas para copiar.

## Decisiones del modelo de datos

- **País y ciudad pertenecen a la revista**, no al cuento: se registran una vez en `revistas.csv`.
  Las revistas sin país aparecen en la portada como «Sin asignar».
- **País de la revista ≠ país del autor.** La navegación va por país de publicación; la búsqueda permite filtrar por ambos.
- **Seudonimo** (de la persona) y **SeudonimoFirma** (cómo firmó ese texto) se mantienen separados.
- **TipoIndice** se muestra como *rúbrica de la revista* (categoría émica) y es la faceta de género.
  **Tipología** es constante en este corpus (*Prosa ficción*): documenta el criterio de selección y
  el build avisa si aparece otro valor.
- **CantitadPaginas** es la superficie de texto medida en páginas de la revista, no el rango de paginación.
  No es comparable entre revistas de formato distinto.
- **Identificadores deterministas**:
  - autor: slug del nombre (`gomez-escobar-francisco`)
  - revista: slug del título (`athenea`)
  - cuento: `revista-fasciculo-pPágina` (`athenea-2-p23`)

  Son la base de las futuras URIs para LOD. Si se corrige el nombre de un autor, su ID cambia:
  conviene fijar los nombres antes de publicar enlaces.

## Diccionario de datos

| Columna original | Campo interno | Entidad |
|---|---|---|
| Colaborador | autor.nombre | autor |
| Sexo | autor.sexo | autor |
| PaisOrigen | autor.pais_origen | autor |
| Seudonimo | autor.seudonimos | autor |
| Título | cuento.titulo | cuento |
| Revista | revista.titulo | revista |
| Fecha Publicación | cuento.fecha (ISO 8601: AAAA, AAAA-MM o AAAA-MM-DD) | cuento |
| Fascículo | cuento.fasciculo | cuento |
| Tipología | cuento.tipologia | cuento |
| Idioma | cuento.idioma | cuento |
| SeudonimoFirma | cuento.firma | cuento |
| TipoIndice | cuento.rubrica | cuento |
| CantitadPaginas | cuento.n_paginas | cuento |
| Paginacion | cuento.paginacion, pag_ini, pag_fin, discontinua | cuento |

## Hoja de ruta

- Palabras y tiempo de lectura (desde el TEI): nuevas columnas en el build, sin cambios en la interfaz.
- Tema y otras categorías analíticas.
- LOD: completar QIDs en `autores.csv` y `revistas.csv`; exportar RDF/JSON-LD desde el build;
  JSON-LD schema.org (`ShortStory`, `Periodical`) en las fichas; sincronización con Wikibase.

## Licencia

Datos: CC BY 4.0 · Código: MIT.
