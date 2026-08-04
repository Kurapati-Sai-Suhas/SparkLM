import React, { useState, useEffect } from 'react';
import Editor from '@monaco-editor/react';
import axios from 'axios';
import ProblemDescription from './ProblemDescription';
import { getAccessToken } from "@/services/api";

const CodingPortal = () => {
    // 1. DYNAMIC STATE
    const [currentProblem, setCurrentProblem] = useState<any>(null);
    const [isLoading, setIsLoading] = useState(true);
    
    const [code, setCode] = useState('');
    const [language, setLanguage] = useState('python');
    const [output, setOutput] = useState('');
    const [isExecuting, setIsExecuting] = useState(false);

    // 2. FETCH QUESTION LOGIC (Extracted so the button can use it!)
    const fetchNextQuestion = async () => {
        setIsLoading(true);
        setOutput(''); // Clear the terminal for the new question
        
        try {
            const token = getAccessToken(); 
            
            const response = await axios.get(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/code/next/`, {
                headers: { 'Authorization': `Bearer ${token}` }
            });
            
            setCurrentProblem(response.data);
            
            // Set default boilerplate dynamically based on the fetched problem title
            setCode(`# Problem: ${response.data.title}\n\ndef solve():\n    # Write your logic here\n    pass\n`);
        } catch (error: any) {
            console.error("Failed to fetch question:", error);
            setOutput(`❌ Failed to load the next question: ${error.response?.data?.error || error.message}`);
        } finally {
            setIsLoading(false);
        }
    };

    // Auto-fetch on initial page load
    useEffect(() => {
        fetchNextQuestion();
    }, []);

    // --- RUN CODE (Single Test) ---
    const handleRunCode = async () => {
        setIsExecuting(true);
        setOutput('🚀 Running your code in Sandbox...');
        
        try {
            const token = getAccessToken(); 
            
            const response = await axios.post(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/code/run/`, {
                code: code,
                language: language,
                stdin: currentProblem.sample_case?.stdin || "" // Sample case from the API (first, public example)
            }, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            const data = response.data;
            if (data.stdout) setOutput(`✅ Execution Successful (${data.time}s, ${data.memory}KB):\n\nOutput: ${data.stdout}`);
            else if (data.compile_output) setOutput(`⚠️ Compilation Error:\n\n${data.compile_output}`);
            else if (data.stderr) setOutput(`❌ Runtime Error:\n\n${data.stderr}`);
            else setOutput(`Status: ${data.status}`);

        } catch (error: any) {
            setOutput(`❌ Connection Error: ${error.response?.data?.error || error.message}`);
        } finally {
            setIsExecuting(false);
        }
    };

    // --- SUBMIT CODE (Elo Rating & Hidden Tests) ---
    const handleSubmitCode = async () => {
        setIsExecuting(true);
        setOutput('⚔️ Running against hidden test cases...');
        
        try {
            const token = getAccessToken(); 
            
            const response = await axios.post(`${import.meta.env.VITE_API_URL || 'http://localhost:8000'}/api/code/submit/`, {
                code: code,
                language: language,
                problem_id: currentProblem.id
            }, {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            const data = response.data;
            let resultText = `STATUS: ${data.status.toUpperCase()} (${data.passed}/${data.total} passed)\n\n`;
            
            data.test_results.forEach((tr: any) => {
                resultText += `Test Case ${tr.test_case}: ${tr.passed ? '✅ PASSED' : '❌ FAILED'} (Expected: ${tr.expected_output}, Got: ${tr.your_output})\n`;
            });

            if (data.elo_update) {
                resultText += `\n🏆 ELO RATING UPDATE:\nOld Rating: ${data.elo_update.old_rating}\nNew Rating: ${data.elo_update.new_rating} (${data.elo_update.rating_change >= 0 ? '+' : ''}${data.elo_update.rating_change})`;
            }

            setOutput(resultText);

        } catch (error: any) {
            setOutput(`❌ Submission Error: ${error.response?.data?.error || error.message}`);
        } finally {
            setIsExecuting(false);
        }
    };

    // --- LOADING & ERROR STATES ---
    if (isLoading) {
        return (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 'calc(100vh - 80px)', backgroundColor: '#121212', color: '#00ff00', fontFamily: 'monospace', fontSize: '18px' }}>
                Loading Adaptive Question...
            </div>
        );
    }

    if (!currentProblem) {
        return (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: 'calc(100vh - 80px)', backgroundColor: '#121212', color: '#ff4444', fontFamily: 'monospace', fontSize: '18px' }}>
                {output || "No questions available or you've solved them all!"}
            </div>
        );
    }

    // --- MAIN UI RENDER ---
    return (
        <div style={{ display: 'flex', height: 'calc(100vh - 80px)', backgroundColor: '#121212', color: '#fff', fontFamily: 'sans-serif' }}>
            
            {/* LEFT PANEL: Problem Description */}
            <div style={{ width: '40%', padding: '20px', borderRight: '1px solid #333', overflowY: 'auto' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h2>{currentProblem.title}</h2>
                    <span style={{ 
                        backgroundColor: currentProblem.difficulty === 'Easy' ? '#28a745' : currentProblem.difficulty === 'Medium' ? '#ffc107' : '#dc3545', 
                        color: currentProblem.difficulty === 'Medium' ? '#000' : '#fff', 
                        padding: '4px 8px', borderRadius: '12px', fontSize: '12px', fontWeight: 'bold' 
                    }}>
                        {currentProblem.difficulty}
                    </span>
                </div>

                {/* XAI TRANSPARENCY BOX */}
                {currentProblem.explanation && (
                    <div style={{ backgroundColor: '#0d2b3e', borderLeft: '4px solid #00a8ff', padding: '12px', marginTop: '15px', borderRadius: '4px' }}>
                        <p style={{ margin: 0, color: '#00a8ff', fontSize: '14px', fontFamily: 'monospace' }}>
                            {currentProblem.explanation}
                        </p>
                    </div>
                )}
                
                {/* Formatting the Kaggle description — content is a mix of
                    real HTML and plain text with literal newlines across
                    the seeded bank; ProblemDescription renders each correctly. */}
                <ProblemDescription
                    content={currentProblem.description}
                    style={{ color: '#ccc', lineHeight: '1.6', marginTop: '15px' }}
                />
                
                {currentProblem.examples?.map((ex: any, idx: number) => (
                    <div key={idx} style={{ backgroundColor: '#1e1e1e', padding: '15px', borderRadius: '8px', marginTop: '15px' }}>
                        <p style={{ margin: '0 0 5px 0', fontWeight: 'bold' }}>Example {idx + 1}:</p>
                        <p style={{ margin: '0', color: '#aaa' }}><strong>Input:</strong> {ex.input}</p>
                        <p style={{ margin: '0', color: '#aaa' }}><strong>Output:</strong> {ex.output}</p>
                    </div>
                ))}
            </div>

            {/* RIGHT PANEL: Editor & Terminal */}
            <div style={{ width: '60%', display: 'flex', flexDirection: 'column' }}>
                
                {/* Top Toolbar */}
                <div style={{ padding: '10px 20px', backgroundColor: '#1e1e1e', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #333' }}>
                    <select 
                        value={language} 
                        onChange={(e) => setLanguage(e.target.value)}
                        style={{ padding: '8px', backgroundColor: '#2d2d2d', color: '#fff', border: '1px solid #444', borderRadius: '4px' }}
                    >
                        <option value="python">Python 3</option>
                        <option value="java">Java</option>
                    </select>
                    
                    <div style={{ display: 'flex', gap: '10px' }}>
                        <button 
                            onClick={handleRunCode} 
                            disabled={isExecuting}
                            style={{ padding: '8px 16px', backgroundColor: '#444', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}
                        >
                            Run Code
                        </button>
                        <button 
                            onClick={handleSubmitCode} 
                            disabled={isExecuting}
                            style={{ padding: '8px 16px', backgroundColor: '#28a745', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                        >
                            Submit
                        </button>
                        {/* 👇 THE NEW BUTTON 👇 */}
                        <button 
                            onClick={fetchNextQuestion} 
                            disabled={isExecuting || isLoading}
                            style={{ padding: '8px 16px', backgroundColor: '#007bff', color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }}
                        >
                            Next Problem ⏭️
                        </button>
                    </div>
                </div>

                {/* Monaco Editor */}
                <div style={{ flexGrow: 1 }}>
                    <Editor
                        height="100%"
                        language={language}
                        theme="vs-dark"
                        value={code}
                        onChange={(value) => setCode(value || '')}
                        options={{ minimap: { enabled: false }, fontSize: 15 }}
                    />
                </div>

                {/* Terminal Output */}
                <div style={{ height: '250px', backgroundColor: '#0d0d0d', color: '#00ff00', padding: '15px', overflowY: 'auto', borderTop: '1px solid #333', fontFamily: 'monospace' }}>
                    <p style={{ margin: '0 0 10px 0', color: '#666' }}>CONSOLE</p>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{output}</pre>
                </div>

            </div>
        </div>
    );
};

export default CodingPortal;