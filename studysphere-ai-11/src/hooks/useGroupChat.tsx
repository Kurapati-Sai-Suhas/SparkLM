import { useState, useEffect, useRef } from 'react';
import { getAccessToken } from "@/services/api";

interface Message {
  sender: string;
  message: string;
}

export function useGroupChat(groupId: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    // 1. Grab the JWT token you saved when the user logged in
    const token = getAccessToken();
    if (!token) {
        console.error("No token found for chat auth");
        return;
    }

    // 2. Connect to the Daphne ASGI server we just started
    const wsUrl = `${import.meta.env.VITE_WS_URL || 'ws://127.0.0.1:8000'}/ws/chat/${groupId}/?token=${token}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    // 3. Listen for connection success
    ws.onopen = () => setIsConnected(true);
    
    // 4. Listen for incoming messages from the server
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      setMessages((prev) => [...prev, { sender: data.sender, message: data.message }]);
    };

    // 5. Handle disconnection
    ws.onclose = () => setIsConnected(false);

    // Cleanup when the component unmounts
    return () => {
      ws.close();
    };
  }, [groupId]);

  // Function to send a new message to the server
  const sendMessage = (message: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ message }));
    }
  };

  return { messages, sendMessage, isConnected };
}