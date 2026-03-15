/// Reimplementação do algoritmo Drain3 em Rust puro.
///
/// Replica exatamente o comportamento do drain3.Drain com:
///   depth=4, sim_th=0.4, max_children=100, parametrize_numeric_tokens=True
///
/// Ref: https://github.com/logpai/Drain3
use std::collections::HashMap;

const PARAM_STR: &str = "<*>";
const MAX_CHILDREN: usize = 100;
/// max_node_depth = depth - 2 = 4 - 2 = 2 (apenas 1 token de prefixo além do nível de contagem)
const MAX_NODE_DEPTH: usize = 2;

/// Nó interno da árvore de prefixos.
#[derive(Default)]
struct Node {
    /// Mapeamento token → índice do nó filho (arena-based).
    children: HashMap<String, usize>,
    /// IDs de clusters armazenados neste nó folha.
    cluster_ids: Vec<usize>,
}

impl Node {
    fn new() -> Self {
        Node::default()
    }
}

/// Cluster de log — corresponde ao LogCluster do Python.
#[allow(dead_code)]
pub struct LogCluster {
    pub cluster_id: usize,
    /// Tokens do template; posições abstraídas são "<*>".
    pub tokens: Vec<String>,
    pub size: usize,
}

impl LogCluster {
    /// Retorna o template como string (tokens separados por espaço).
    pub fn get_template(&self) -> String {
        self.tokens.join(" ")
    }
}

/// Implementação Drain3 com arena de nós para evitar problemas de borrow.
pub struct Drain3 {
    /// Arena de nós; índice 0 é a raiz.
    nodes: Vec<Node>,
    /// Mapeamento cluster_id → LogCluster.
    pub clusters: HashMap<usize, LogCluster>,
    /// Contador de clusters (equivalente a drain.clusters_counter).
    counter: usize,
    pub sim_th: f64,
}

impl Drain3 {
    /// Cria nova instância com parâmetros equivalentes ao Python:
    ///   sim_th=0.4, depth=4, parametrize_numeric_tokens=True
    pub fn new(sim_th: f64) -> Self {
        let mut nodes = Vec::new();
        nodes.push(Node::new()); // índice 0 = raiz
        Drain3 {
            nodes,
            clusters: HashMap::new(),
            counter: 0,
            sim_th,
        }
    }

    /// Processa uma mensagem de log e retorna (cluster_id, template).
    /// Equivalente a drain.add_log_message() no Python.
    pub fn add_log_message(&mut self, content: &str) -> (usize, String) {
        let tokens = Self::tokenize(content);

        let match_id = self.tree_search(&tokens);

        match match_id {
            None => {
                // Nenhum cluster existente — criar novo
                self.counter += 1;
                let id = self.counter;
                let template = tokens.join(" ");
                let cluster = LogCluster {
                    cluster_id: id,
                    tokens: tokens.clone(),
                    size: 1,
                };
                self.clusters.insert(id, cluster);
                self.add_seq_to_prefix_tree(id, &tokens);
                (id, template)
            }
            Some(match_id) => {
                let cluster = self.clusters.get_mut(&match_id).unwrap();
                let new_tokens = Self::create_template(&tokens, &cluster.tokens);
                if new_tokens != cluster.tokens {
                    cluster.tokens = new_tokens;
                }
                cluster.size += 1;
                let template = cluster.get_template();
                (match_id, template)
            }
        }
    }

    /// Tokeniza o conteúdo: strip + split por whitespace.
    pub fn tokenize(content: &str) -> Vec<String> {
        content.split_whitespace().map(|s| s.to_string()).collect()
    }

    /// Retorna true se o token contém algum dígito ASCII.
    fn has_numbers(s: &str) -> bool {
        s.bytes().any(|b| b.is_ascii_digit())
    }

    /// Busca na árvore de prefixos o cluster mais similar.
    /// Equivalente a tree_search() + fast_match() do Python.
    fn tree_search(&self, tokens: &[String]) -> Option<usize> {
        let token_count = tokens.len();
        let count_key = token_count.to_string();

        // Nível 1: por contagem de tokens
        let first_layer_idx = match self.nodes[0].children.get(&count_key) {
            Some(&idx) => idx,
            None => return None,
        };

        if token_count == 0 {
            // Log vazio — retorna único cluster do nó
            return self.nodes[first_layer_idx]
                .cluster_ids
                .first()
                .copied()
                .filter(|id| self.clusters.contains_key(id));
        }

        let mut cur_idx = first_layer_idx;

        // Navegar prefixo até max_node_depth
        let mut cur_depth = 1usize;
        for token in tokens {
            if cur_depth >= MAX_NODE_DEPTH {
                break;
            }
            if cur_depth == token_count {
                break;
            }

            let node = &self.nodes[cur_idx];
            if let Some(&child_idx) = node.children.get(token.as_str()) {
                cur_idx = child_idx;
            } else if let Some(&child_idx) = node.children.get(PARAM_STR) {
                cur_idx = child_idx;
            } else {
                return None;
            }

            cur_depth += 1;
        }

        // Coletar cluster_ids do nó folha e encontrar melhor match
        let cluster_ids: Vec<usize> = self.nodes[cur_idx].cluster_ids.clone();
        self.fast_match(&cluster_ids, tokens)
    }

    /// Seleciona o cluster com maior similaridade acima de sim_th.
    /// Equivalente a fast_match() do Python (include_params=False).
    fn fast_match(&self, cluster_ids: &[usize], tokens: &[String]) -> Option<usize> {
        let mut max_sim: f64 = -1.0;
        let mut max_param_count: i64 = -1;
        let mut best_id: Option<usize> = None;

        for &id in cluster_ids {
            let cluster = match self.clusters.get(&id) {
                Some(c) => c,
                None => continue,
            };

            let (sim, param_count) = Self::get_seq_distance(&cluster.tokens, tokens, false);

            if sim > max_sim || (sim == max_sim && (param_count as i64) > max_param_count) {
                max_sim = sim;
                max_param_count = param_count as i64;
                best_id = Some(id);
            }
        }

        if max_sim >= self.sim_th {
            best_id
        } else {
            None
        }
    }

    /// Calcula similaridade entre template (seq1) e log (seq2).
    /// Equivalente a get_seq_distance() do Python.
    /// seq1 = template (pode conter "<*>"), seq2 = tokens do log.
    fn get_seq_distance(seq1: &[String], seq2: &[String], include_params: bool) -> (f64, usize) {
        if seq1.is_empty() {
            return (1.0, 0);
        }

        let mut sim_tokens = 0usize;
        let mut param_count = 0usize;

        for (t1, t2) in seq1.iter().zip(seq2.iter()) {
            if t1 == PARAM_STR {
                param_count += 1;
                continue;
            }
            if t1 == t2 {
                sim_tokens += 1;
            }
        }

        if include_params {
            sim_tokens += param_count;
        }

        let sim = sim_tokens as f64 / seq1.len() as f64;
        (sim, param_count)
    }

    /// Cria novo template fundindo tokens do log com template existente.
    /// Posições divergentes viram "<*>".
    /// Equivalente a create_template(seq1=new_log, seq2=template) do Python.
    fn create_template(new_tokens: &[String], template: &[String]) -> Vec<String> {
        template
            .iter()
            .zip(new_tokens.iter())
            .map(|(t, n)| if t == n { t.clone() } else { PARAM_STR.to_string() })
            .collect()
    }

    /// Insere cluster na árvore de prefixos.
    /// Equivalente a add_seq_to_prefix_tree() do Python.
    fn add_seq_to_prefix_tree(&mut self, cluster_id: usize, tokens: &[String]) {
        let token_count = tokens.len();
        let count_key = token_count.to_string();

        // Nível 1: nó de contagem de tokens
        let first_layer_idx = self.get_or_create_child(0, &count_key);
        let mut cur_idx = first_layer_idx;

        if token_count == 0 {
            self.nodes[cur_idx].cluster_ids = vec![cluster_id];
            return;
        }

        let mut current_depth = 1usize;
        for token in tokens {
            if current_depth >= MAX_NODE_DEPTH || current_depth >= token_count {
                // Nó folha: limpar IDs obsoletos e adicionar novo cluster
                self.nodes[cur_idx]
                    .cluster_ids
                    .retain(|id| self.clusters.contains_key(id));
                self.nodes[cur_idx].cluster_ids.push(cluster_id);
                break;
            }

            // Decisão de roteamento para o próximo nó
            let has_nums = Self::has_numbers(token);
            let token_exists = self.nodes[cur_idx].children.contains_key(token.as_str());
            let param_exists = self.nodes[cur_idx].children.contains_key(PARAM_STR);
            let children_len = self.nodes[cur_idx].children.len();

            let next_idx = if !token_exists {
                if has_nums {
                    // Token numérico → sempre rotear para nó "<*>"
                    self.get_or_create_child(cur_idx, PARAM_STR)
                } else if param_exists {
                    if children_len < MAX_CHILDREN {
                        // Ainda há espaço para nó exato
                        self.get_or_create_child(cur_idx, token)
                    } else {
                        // Capacidade máxima → usar nó "<*>" existente
                        *self.nodes[cur_idx].children.get(PARAM_STR).unwrap()
                    }
                } else {
                    // Ainda não há nó "<*>"
                    if children_len + 1 < MAX_CHILDREN {
                        self.get_or_create_child(cur_idx, token)
                    } else if children_len + 1 == MAX_CHILDREN {
                        // Último slot → criar nó "<*>"
                        self.get_or_create_child(cur_idx, PARAM_STR)
                    } else {
                        // Já na capacidade máxima — nó "<*>" deve existir
                        *self.nodes[cur_idx].children.get(PARAM_STR).unwrap()
                    }
                }
            } else {
                *self.nodes[cur_idx].children.get(token.as_str()).unwrap()
            };

            cur_idx = next_idx;
            current_depth += 1;
        }
    }

    /// Retorna índice do filho com chave `key`, criando-o se necessário.
    fn get_or_create_child(&mut self, parent_idx: usize, key: &str) -> usize {
        if let Some(&idx) = self.nodes[parent_idx].children.get(key) {
            return idx;
        }
        let new_idx = self.nodes.len();
        self.nodes.push(Node::new());
        self.nodes[parent_idx].children.insert(key.to_string(), new_idx);
        new_idx
    }
}
