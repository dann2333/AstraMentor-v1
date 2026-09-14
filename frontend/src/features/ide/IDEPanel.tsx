import { useState } from 'react';
import { CodeEditor } from '../../components/CodeEditor';
import { Button } from '../../components/ui/button';
import { Play, RotateCcw, Terminal } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { api } from '../../api/client';
import { toast } from 'sonner';

interface IDEPanelProps {
    initialCode?: string;
    initialLanguage?: string;
}

export function IDEPanel({ initialCode = "", initialLanguage = "python" }: IDEPanelProps) {
    const [code, setCode] = useState(initialCode);
    const [language, setLanguage] = useState(initialLanguage);
    const [output, setOutput] = useState("");
    const [isRunning, setIsRunning] = useState(false);

    const handleRun = async () => {
        if (!code.trim()) return;
        setIsRunning(true);
        setOutput("运行中…");
        
        try {
            const result = await api.runCode(code, language);
            if (result.error) {
                setOutput(`Error:\n${result.error}`);
            } else {
                setOutput(result.output || "（没有输出）");
            }
        } catch (error) {
            console.error(error);
            toast.error("代码没跑起来");
            setOutput("运行环境出错了");
        } finally {
            setIsRunning(false);
        }
    };

    return (
        <div className="h-full flex flex-col bg-background">
            {/* Toolbar */}
            <div className="flex items-center justify-between p-2 border-b bg-muted/40">
                <div className="flex items-center gap-2">
                    <Select value={language} onValueChange={setLanguage}>
                        <SelectTrigger className="w-[120px] h-8 text-xs">
                            <SelectValue placeholder="语言" />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectItem value="python">Python</SelectItem>
                            <SelectItem value="javascript">JavaScript</SelectItem>
                            <SelectItem value="go">Go</SelectItem>
                            <SelectItem value="c">C</SelectItem>
                            <SelectItem value="cpp">C++</SelectItem>
                            <SelectItem value="java">Java</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div className="flex items-center gap-2">
                    <Button 
                        size="sm" 
                        variant="ghost" 
                        onClick={() => setCode("")}
                        className="h-8 w-8 p-0"
                        title="恢复初始代码"
                    >
                        <RotateCcw className="h-4 w-4" />
                    </Button>
                    <Button 
                        size="sm" 
                        onClick={handleRun} 
                        disabled={isRunning}
                        className="h-8 text-xs"
                    >
                        {isRunning ? "运行中…" : (
                            <>
                                <Play className="w-3 h-3 mr-1.5" />
                                运行
                            </>
                        )}
                    </Button> 
                </div>
            </div>

            {/* Editor Area */}
            <div className="flex-1 min-h-0">
                 <CodeEditor 
                    language={language}
                    code={code}
                    onChange={(val) => setCode(val || "")}
                    theme="vs-dark"
                />
            </div>

            {/* Console Output */}
            <div className="flex h-[30%] flex-col border-t border-foreground/10 bg-[#0b0a13] text-[#e9e2d2]">
                <div className="flex items-center gap-2 border-b border-white/10 bg-[#141220] px-3 py-1.5 font-mono text-xs text-[#a99db8]">
                    <Terminal className="w-3 h-3" />
                    <span>输出</span>
                </div>
                <div className="flex-1 p-3 font-mono text-sm overflow-auto whitespace-pre-wrap">
                    {output || <span className="italic text-[#6b6280]">还没运行</span>}
                </div>
            </div>
        </div>
    );
}
