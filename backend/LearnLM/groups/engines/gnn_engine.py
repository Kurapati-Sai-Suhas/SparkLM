import os
import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import GCNConv
from django.utils import timezone
from groups.models import UserTopicMastery
import onnxruntime as ort

os.makedirs("models_data", exist_ok=True)

class TrueGCNKnowledgeGraph(nn.Module):
    """
    Phase 3: Real Graph Convolutional Network using PyTorch Geometric.
    Learns spatial dependencies and mathematically propagates decay penalties.
    """
    def __init__(self, num_node_features=4, hidden_channels=16):
        super(TrueGCNKnowledgeGraph, self).__init__()
        self.conv1 = GCNConv(num_node_features, hidden_channels)
        self.conv2 = GCNConv(hidden_channels, hidden_channels // 2)
        self.out = nn.Linear(hidden_channels // 2, 1)
        
    def forward(self, x, edge_index):
        # x: Node feature matrix [num_nodes, num_node_features]
        # edge_index: Graph connectivity [2, num_edges]
        
        x = self.conv1(x, edge_index)
        x = F.relu(x)
        x = F.dropout(x, p=0.2, training=self.training)
        
        x = self.conv2(x, edge_index)
        x = F.relu(x)
        
        # Predict success probability per node
        out = torch.sigmoid(self.out(x))
        return out

def train_and_save_gcn(subject_name, data_dict):
    """
    Trains the GCN on synthetic student data (from synthetic_data_generator).
    """
    model = TrueGCNKnowledgeGraph()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    criterion = nn.BCELoss()
    
    x = data_dict["x"] # [num_students, num_nodes, features]
    y = data_dict["y"] # [num_students, num_nodes, 1]
    edge_index = data_dict["edge_index"]
    
    model.train()
    for epoch in range(50):
        total_loss = 0
        for i in range(len(x)):
            optimizer.zero_grad()
            out = model(x[i], edge_index)
            loss = criterion(out, y[i])
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            
    torch.save(model.state_dict(), f"models_data/gcn_{subject_name}.pth")
    return model

def load_gcn(subject_name):
    model = TrueGCNKnowledgeGraph()
    path = f"models_data/gcn_{subject_name}.pth"
    if os.path.exists(path):
        model.load_state_dict(torch.load(path, weights_only=True))
    model.eval()
    return model

class GNNKnowledgeGraph:
    def __init__(self, subject="dsa", graph=None):
        self.subject = subject
        self.graph = graph
        self.model = None
        self.ort_session = None
        
        onnx_path = f"models_data/gcn_{subject}.onnx"
        if os.path.exists(onnx_path):
            try:
                self.ort_session = ort.InferenceSession(onnx_path)
                print(f"⚡ Loaded ONNX Runtime for {subject}")
            except Exception as e:
                print(f"⚠️ ONNX load failed, falling back to PyTorch: {e}")
                self.model = load_gcn(subject)
        else:
            self.model = load_gcn(subject)
        
        if graph:
            self.nodes = list(graph.nodes)
            self.node_to_idx = {n: i for i, n in enumerate(self.nodes)}
            
            edges = []
            for u, v in graph.edges:
                edges.append([self.node_to_idx[u], self.node_to_idx[v]])
            self.edge_index = torch.tensor(edges, dtype=torch.long).t().contiguous()
            if self.edge_index.numel() == 0:
                self.edge_index = torch.empty((2, 0), dtype=torch.long)
        else:
            self.nodes = []
            self.node_to_idx = {}
            self.edge_index = torch.empty((2, 0), dtype=torch.long)

    def get_next_optimal_topic(self, user):
        if not self.nodes:
            return {"recommended_topic": None, "reason": "Empty graph"}
            
        mastery_records = UserTopicMastery.objects.filter(user=user).select_related('topic')
        # Keyed by topic NAME — the graph nodes are name strings. Keying by
        # the Topic object meant every lookup below missed, so the GCN
        # always saw cold-start features for every node.
        mastery_dict = {m.topic.name: m for m in mastery_records}
        
        # Build full graph state for GCN
        node_features = []
        for node in self.nodes:
            record = mastery_dict.get(node)
            if record:
                acc = record.accuracy
                reviews = record.reviews
                elo_percentile = max(0.0, min(1.0, (record.elo_rating - 800) / 1000.0))
                days_since = (timezone.now() - record.last_practiced).days
            else:
                acc, reviews, elo_percentile, days_since = 0.5, 0, 0.4, 0
                
            stability = 1.0 + (reviews * 0.5)
            retention = math.exp(-days_since / stability) if reviews > 0 else 0.5
            volume_score = math.tanh(reviews / 10.0)
            
            node_features.append([acc, retention, volume_score, elo_percentile])
            
        x_tensor = torch.tensor(node_features, dtype=torch.float32)
        
        # Inference
        if self.ort_session:
            ort_inputs = {
                'node_features': x_tensor.numpy(),
                'edge_index': self.edge_index.numpy()
            }
            ort_outs = self.ort_session.run(None, ort_inputs)
            predictions = ort_outs[0].squeeze().tolist()
        else:
            # PyTorch Geometric Forward Pass Fallback
            with torch.no_grad():
                predictions = self.model(x_tensor, self.edge_index).squeeze().tolist()
            
        # If single node, convert to list
        if isinstance(predictions, float):
            predictions = [predictions]
            
        # Find best unmastered topic where prerequisites are met
        candidate_probs = {}
        for i, node in enumerate(self.nodes):
            if node not in mastery_dict or mastery_dict[node].accuracy < 0.8:
                # Check prereqs
                prereqs = list(self.graph.predecessors(node))
                if all(p in mastery_dict and mastery_dict[p].accuracy >= 0.8 for p in prereqs):
                    candidate_probs[node] = predictions[i]
                    
        if not candidate_probs:
            # Fallback to root nodes
            roots = [n for n in self.nodes if self.graph.in_degree(n) == 0]
            for r in roots:
                if r not in mastery_dict or mastery_dict[r].accuracy < 0.8:
                    candidate_probs[r] = predictions[self.node_to_idx[r]]
                    
        if not candidate_probs:
            return {"recommended_topic": self.nodes[-1], "reason": "All topics mastered!"}
            
        best_topic = max(candidate_probs, key=candidate_probs.get)
        prob = candidate_probs[best_topic]
        
        xai_text = f"🧠 XAI Insight: Recommended '{best_topic}' — Graph Convolution Network predicts {prob*100:.1f}% success based on spatial prerequisite mastery."
        
        return {
            "recommended_topic": best_topic,
            "reason": xai_text,
            "xai": {
                "success_probability": round(prob * 100, 1)
            }
        }