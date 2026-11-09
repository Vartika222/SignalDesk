-- One partial HNSW index per embedding model. Queries must use the same cast and model filter
-- to hit the index:  WHERE model = 'bge-small' ORDER BY vector::vector(384) <=> $1::vector(384)
CREATE INDEX embeddings_hnsw_bge_small ON embeddings
    USING hnsw ((vector::vector(384)) vector_cosine_ops) WHERE model = 'bge-small';
CREATE INDEX embeddings_hnsw_bge_base ON embeddings
    USING hnsw ((vector::vector(768)) vector_cosine_ops) WHERE model = 'bge-base';
