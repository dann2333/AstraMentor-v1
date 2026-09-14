
import { ScrollArea } from '../../components/ui/scroll-area';
import { Button } from '../../components/ui/button';
import { MessageSquare, Calendar, ChevronRight, Trash2, X } from 'lucide-react';
import { cn } from '../../lib/utils';
import { formatDistanceToNow } from 'date-fns';
import { zhCN, enUS } from 'date-fns/locale';
import { useLanguage } from '../../contexts/LanguageContext';

export interface GraphSession {
    id: string;
    topic: string;
    date: string;
    // We don't need full data here, just metadata for the list
    averageMastery?: number; // 0-1
}

interface HistorySidebarProps {
    isOpen: boolean;
    sessions: GraphSession[];
    currentSessionId: string | null;
    onSelectSession: (sessionId: string) => void;
    onDeleteSession: (sessionId: string) => void;
    onClose: () => void;
}

export function HistorySidebar({ 
    isOpen, 
    sessions, 
    currentSessionId, 
    onSelectSession, 
    onDeleteSession,
    onClose,
}: HistorySidebarProps) {
    const { t, language } = useLanguage();
    if (!isOpen) return null;

    return (
        <div className="w-full h-full flex flex-col shrink-0 session-sidebar bg-transparent">
            <div className="p-4 border-b flex justify-between items-center bg-muted/30">
                <h2 className="font-semibold text-lg flex items-center gap-2">
                    <MessageSquare className="h-4 w-4 text-accent" />
                    {t('app.history_sidebar')}
                </h2>
                <Button variant="ghost" size="icon" onClick={onClose} aria-label={t('common.close')}>
                    <X className="h-4 w-4" />
                </Button>
            </div>
            
            <ScrollArea className="flex-1 pl-4 pr-5 py-4">
                <div className="space-y-3 overflow-hidden">
                    {sessions.length === 0 ? (
                        <div className="text-center text-muted-foreground py-8 text-sm">
                            {t('app.no_history')}
                        </div>
                    ) : (
                        sessions.map(session => (
                            <div 
                                key={session.id}
                                onClick={() => onSelectSession(session.id)}
                                className={cn(
                                    // 卡片本身也走玻璃，否则一块实色卡贴在玻璃侧栏上很突兀；
                                    // hover 原来是 bg-accent —— 整块琥珀实色，鼠标扫过去像闪了一下。
                                    "group relative flex cursor-pointer items-center justify-between overflow-hidden rounded-[var(--glass-radius-md)] p-3 transition-all",
                                    currentSessionId === session.id
                                        ? "glass glass--thin glass--grain bg-accent/12 shadow-[inset_0_0_0_1px_hsl(var(--accent)/.45)]"
                                        : "glass glass--thin glass--grain hover:bg-foreground/[0.07]"
                                )}
                            >
                                <div className="flex flex-col gap-1 overflow-hidden flex-1 w-0 mr-2">
                                    <span
                                        title={session.topic}
                                        className={cn(
                                            "font-medium block truncate",
                                            currentSessionId === session.id ? "text-accent" : "text-foreground"
                                        )}
                                    >
                                        {session.topic}
                                    </span>
                                    <div className="flex items-center text-xs text-muted-foreground gap-1 mb-1">
                                        <Calendar className="w-3 h-3" />
                                        {formatDistanceToNow(new Date(session.date), { addSuffix: true, locale: language === 'zh' ? zhCN : enUS })}
                                    </div>
                                    
                                    {/* Progress Bar */}
                                    <div className="flex items-center gap-2 mt-1">
                                        <div className="flex-1 h-1.5 bg-secondary/50 rounded-full overflow-hidden">
                                            <div 
                                                className="h-full rounded-full bg-gradient-to-r from-primary to-accent transition-all duration-500"
                                                style={{ width: `${Math.round((session.averageMastery || 0) * 100)}%` }}
                                            />
                                        </div>
                                        <span className="text-[10px] text-muted-foreground shrink-0">
                                            {Math.round((session.averageMastery || 0) * 100)}%
                                        </span>
                                    </div>
                                </div>
                                
                                <div className="flex items-center gap-1">
                                     {currentSessionId === session.id && (
                                        <ChevronRight className="h-4 w-4 text-accent" />
                                     )}
                                     <Button
                                        variant="ghost"
                                        size="icon"
                                        title={t('app.delete_history')}
                                        className="h-6 w-6 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-destructive/15 hover:text-destructive"
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onDeleteSession(session.id);
                                        }}
                                     >
                                         <Trash2 className="w-3.5 h-3.5" />
                                     </Button>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </ScrollArea>
        </div>
    );
}
