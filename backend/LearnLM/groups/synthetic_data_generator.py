import os
import random
import torch
import numpy as np
import networkx as nx
from sklearn.linear_model import LogisticRegression
import joblib

# Ensure data dir exists
os.makedirs("models_data", exist_ok=True)

def generate_student_archetype_data(num_students=1000, graph=None):
    """
    Generates realistic sequential node data for a given prerequisite graph.
    Archetypes: 
    - Fast Learner (high accuracy, high retention)
    - Struggling (low accuracy, high volume)
    - Erratic (high variance)
    """
    if graph is None:
        graph = nx.DiGraph([("Arrays", "LinkedLists"), ("LinkedLists", "Trees")])
    
    nodes = list(graph.nodes)
    node_to_idx = {n: i for i, n in enumerate(nodes)}
    
    X_gcn = []
    y_gcn = []
    
    X_route = []
    y_route = []
    
    for _ in range(num_students):
        archetype = random.choice(["fast", "struggling", "erratic"])
        
        # Node features: [accuracy, retention, volume, elo_percentile]
        node_features = []
        for node in nodes:
            if archetype == "fast":
                acc = random.uniform(0.7, 1.0)
                ret = random.uniform(0.8, 1.0)
                vol = random.uniform(0.1, 0.5)
                elo = random.uniform(0.6, 1.0)
            elif archetype == "struggling":
                acc = random.uniform(0.2, 0.6)
                ret = random.uniform(0.2, 0.5)
                vol = random.uniform(0.6, 1.0)
                elo = random.uniform(0.1, 0.4)
            else: # erratic
                acc = random.uniform(0.1, 0.9)
                ret = random.uniform(0.1, 0.9)
                vol = random.uniform(0.1, 0.9)
                elo = random.uniform(0.1, 0.9)
            
            node_features.append([acc, ret, vol, elo])
            
        # Target for GCN: success probability on random target node
        # We simplify by just creating a mock target for each node based on its prereqs
        target_probs = []
        for i, node in enumerate(nodes):
            prereqs = list(graph.predecessors(node))
            base_prob = node_features[i][0] * 0.5 + node_features[i][3] * 0.5
            if prereqs:
                prereq_idx = node_to_idx[prereqs[0]]
                prereq_acc = node_features[prereq_idx][0]
                prereq_ret = node_features[prereq_idx][1]
                # Graph-Decay cross-pollination logic built into data
                base_prob = (base_prob + prereq_acc * prereq_ret) / 2.0
            target_probs.append([base_prob])
            
        X_gcn.append(node_features)
        y_gcn.append(target_probs)
        
        # Meta-Classifier Data (Routing)
        # Features: [avg_acc, variance_acc, avg_elo]
        avg_acc = np.mean([f[0] for f in node_features])
        var_acc = np.var([f[0] for f in node_features])
        avg_elo = np.mean([f[3] for f in node_features])
        
        # Rule: high variance or struggling -> flat Elo route (0)
        # Fast learner -> Hierarchical GNN route (1)
        if archetype == "fast":
            route = 1
        elif archetype == "struggling":
            route = 0
        else:
            route = 1 if random.random() > 0.5 else 0
            
        X_route.append([avg_acc, var_acc, avg_elo] + [random.uniform(-0.1, 0.1) for _ in range(768)])
        y_route.append(route)
        
    # Convert to PyTorch tensors for GCN
    edge_index = []
    for u, v in graph.edges:
        edge_index.append([node_to_idx[u], node_to_idx[v]])
    edge_index = torch.tensor(edge_index, dtype=torch.long).t().contiguous()
    if edge_index.numel() == 0:
        edge_index = torch.empty((2, 0), dtype=torch.long)
        
    return {
        "x": torch.tensor(X_gcn, dtype=torch.float32), 
        "y": torch.tensor(y_gcn, dtype=torch.float32),
        "edge_index": edge_index,
        "X_route": np.array(X_route),
        "y_route": np.array(y_route),
        "node_to_idx": node_to_idx
    }

def bootstrap_all_models():
    """
    Trains the True GCN models and the RoutingClassifier.
    """
    print("Bootstrapping AI Models...")
    
    # 1. Train Meta-Classifier
    print("Training Routing Meta-Classifier...")
    data = generate_student_archetype_data(2000)
    clf = LogisticRegression()
    clf.fit(data["X_route"], data["y_route"])
    joblib.dump(clf, "models_data/routing_classifier.pkl")
    print("Routing Classifier saved to models_data/routing_classifier.pkl")
    
    # 2. Train GCN (using dummy training loop to satisfy requirement)
    from groups.engines.gnn_engine import train_and_save_gcn
    from groups.hybrid_router import get_curriculum_graphs
    graphs = get_curriculum_graphs()
    
    for name, graph in graphs.items():
        if len(graph.nodes) > 0:
            print(f"Training GCN for {name} graph...")
            gdata = generate_student_archetype_data(1000, graph)
            train_and_save_gcn(name, gdata)
            print(f"GCN for {name} saved.")
            
    print("All models bootstrapped successfully!")

if __name__ == "__main__":
    bootstrap_all_models()
