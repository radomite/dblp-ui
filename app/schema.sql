CREATE TABLE publications (
  id INTEGER PRIMARY KEY,
  dblp_key TEXT NOT NULL UNIQUE,
  record_type TEXT NOT NULL,
  title TEXT NOT NULL,
  authors TEXT NOT NULL,
  year INTEGER,
  venue TEXT,
  venue_aliases TEXT NOT NULL,
  doi TEXT,
  ee TEXT,
  mdate TEXT,
  bibtex TEXT NOT NULL
);
CREATE TABLE publication_authors (
  publication_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  pid TEXT,
  name_norm TEXT NOT NULL
);
CREATE VIRTUAL TABLE pub_fts USING fts5(
  title, authors, venue, venue_aliases, content='publications', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE title_grams USING fts5(
  title, content='publications', content_rowid='id',
  tokenize='trigram', detail='none'
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
