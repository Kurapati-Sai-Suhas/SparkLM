import os
import torch
from groups.engines.gnn_engine import TrueGCNKnowledgeGraph
from groups.hybrid_router import get_curriculum_graphs

def export_all_to_onnx():
    print("Starting ONNX Compilation for GCN Models...")
    os.makedirs("models_data", exist_ok=True)

    graphs = get_curriculum_graphs()
    
    for subject, graph in graphs.items():
        if len(graph.nodes) == 0:
            continue
            
        pth_path = f"models_data/gcn_{subject}.pth"
        onnx_path = f"models_data/gcn_{subject}.onnx"
        
        if not os.path.exists(pth_path):
            print(f"Warning: Could not find {pth_path}. Run synthetic_data_generator first.")
            continue
            
        print(f"Loading {pth_path}...")
        model = TrueGCNKnowledgeGraph()
        model.load_state_dict(torch.load(pth_path, weights_only=True))
        model.eval()
        
        # Prepare dummy inputs matching the shape of the forward pass
        num_nodes = len(graph.nodes)
        
        # x shape: [num_nodes, 4 features]
        dummy_x = torch.randn(num_nodes, 4)
        
        # edge_index shape: [2, num_edges]
        node_to_idx = {n: i for i, n in enumerate(graph.nodes)}
        edges = []
        for u, v in graph.edges:
            edges.append([node_to_idx[u], node_to_idx[v]])
        
        if not edges:
            dummy_edge_index = torch.empty((2, 0), dtype=torch.long)
        else:
            dummy_edge_index = torch.tensor(edges, dtype=torch.long).t().contiguous()
            
        print(f"Exporting {subject} model to ONNX...")
        try:
            torch.onnx.export(
                model,                                # model being run
                (dummy_x, dummy_edge_index),          # model input (or a tuple for multiple inputs)
                onnx_path,                            # where to save the model
                export_params=True,                   # store the trained parameter weights inside the model file
                opset_version=14,                     # the ONNX version to export the model to
                do_constant_folding=True,             # whether to execute constant folding for optimization
                input_names=['node_features', 'edge_index'], # the model's input names
                output_names=['success_probability'], # the model's output names
                dynamic_axes={
                    'node_features': {0: 'num_nodes'},
                    'edge_index': {1: 'num_edges'},
                    'success_probability': {0: 'num_nodes'}
                }
            )
            print(f"Successfully exported to {onnx_path}")
        except Exception as e:
            print(f"Failed to export {subject}: {e}")
            
    print("ONNX Compilation complete!")

if __name__ == "__main__":
    export_all_to_onnx()
