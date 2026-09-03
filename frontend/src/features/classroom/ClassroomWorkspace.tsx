/**
 * 班级工作台：老师建班/布置/批改，学生入班/交作业/看分数。
 *
 * 展示哪一侧完全由账号角色决定，但真正的授权在后端：这里少判一次条件，
 * 接口也只会返回 403/404，不会漏数据。
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw, Trash2 } from 'lucide-react';
import { Button } from '../../components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { ScrollArea } from '../../components/ui/scroll-area';
import { readableApiError } from '../../api/errors';
import {
    classroomApi,
    type Assignment,
    type Classroom,
    type StudentAssignment,
    type StudentProgress,
    type Submission,
} from '../../api/classrooms';
import { useAuth } from '../../contexts/AuthContext';

interface ClassroomWorkspaceProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onRequestLogin: () => void;
}

function formatDate(value: string | null): string {
    if (!value) return '不限';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function ClassroomWorkspace({
    open,
    onOpenChange,
    onRequestLogin,
}: ClassroomWorkspaceProps) {
    const { isAuthenticated, isTeacher } = useAuth();

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-4xl">
                <DialogHeader>
                    <DialogTitle>班级与作业</DialogTitle>
                    <DialogDescription>
                        {isTeacher
                            ? '管理你的班级、布置作业并批改学生提交。'
                            : '加入班级、查看作业并提交你的答案。'}
                    </DialogDescription>
                </DialogHeader>

                {!isAuthenticated ? (
                    <div className="space-y-4 py-6 text-center">
                        <p className="text-sm text-muted-foreground">
                            班级与作业需要登录后使用。访客数据是共享的，无法用来记名。
                        </p>
                        <Button onClick={onRequestLogin}>去登录</Button>
                    </div>
                ) : isTeacher ? (
                    <TeacherView />
                ) : (
                    <StudentView />
                )}
            </DialogContent>
        </Dialog>
    );
}

// ----------------------------------------------------------------------
// 老师侧
// ----------------------------------------------------------------------
function TeacherView() {
    const [classrooms, setClassrooms] = useState<Classroom[]>([]);
    const [selectedId, setSelectedId] = useState<string | null>(null);
    const [assignments, setAssignments] = useState<Assignment[]>([]);
    const [progress, setProgress] = useState<StudentProgress[]>([]);
    const [openAssignmentId, setOpenAssignmentId] = useState<string | null>(null);
    const [submissions, setSubmissions] = useState<Submission[]>([]);
    const [newClassName, setNewClassName] = useState('');
    const [newAssignment, setNewAssignment] = useState({ title: '', instructions: '' });
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const selected = useMemo(
        () => classrooms.find((item) => item.id === selectedId) ?? null,
        [classrooms, selectedId],
    );

    const run = useCallback(async (action: () => Promise<void>, fallback: string) => {
        setBusy(true);
        setError('');
        try {
            await action();
        } catch (caught) {
            setError(readableApiError(caught, fallback));
        } finally {
            setBusy(false);
        }
    }, []);

    const loadClassrooms = useCallback(async () => {
        const list = await classroomApi.listTaught();
        setClassrooms(list);
        setSelectedId((current) => current ?? list[0]?.id ?? null);
    }, []);

    useEffect(() => {
        void run(loadClassrooms, '无法加载班级');
    }, [run, loadClassrooms]);

    useEffect(() => {
        if (!selectedId) {
            setAssignments([]);
            setProgress([]);
            return;
        }
        void run(async () => {
            const [items, students] = await Promise.all([
                classroomApi.listClassroomAssignments(selectedId),
                classroomApi.classroomProgress(selectedId),
            ]);
            setAssignments(items);
            setProgress(students);
            setOpenAssignmentId(null);
            setSubmissions([]);
        }, '无法加载班级详情');
    }, [selectedId, run]);

    const openSubmissions = (assignmentId: string) =>
        run(async () => {
            setOpenAssignmentId(assignmentId);
            setSubmissions(await classroomApi.listSubmissions(assignmentId));
        }, '无法加载提交');

    const openAssignment = assignments.find((item) => item.id === openAssignmentId) ?? null;

    return (
        <div className="space-y-4">
            {error && <p className="text-sm text-red-500" role="alert">{error}</p>}

            <div className="flex flex-wrap items-end gap-2">
                <div className="flex-1 min-w-[200px] space-y-1">
                    <Label htmlFor="new-class-name">新建班级</Label>
                    <Input
                        id="new-class-name"
                        value={newClassName}
                        placeholder="例如：2026 春 算法入门"
                        maxLength={80}
                        onChange={(event) => setNewClassName(event.target.value)}
                    />
                </div>
                <Button
                    disabled={busy || !newClassName.trim()}
                    onClick={() =>
                        run(async () => {
                            const created = await classroomApi.createClassroom(newClassName.trim());
                            setNewClassName('');
                            await loadClassrooms();
                            setSelectedId(created.id);
                        }, '建班失败')
                    }
                >
                    建班
                </Button>
            </div>

            {classrooms.length === 0 ? (
                <p className="text-sm text-muted-foreground">还没有班级，先建一个吧。</p>
            ) : (
                <div className="flex flex-wrap gap-2">
                    {classrooms.map((item) => (
                        <Button
                            key={item.id}
                            size="sm"
                            variant={item.id === selectedId ? 'default' : 'outline'}
                            onClick={() => setSelectedId(item.id)}
                        >
                            {item.name}
                            <span className="ml-2 text-xs opacity-70">{item.member_count} 人</span>
                        </Button>
                    ))}
                </div>
            )}

            {selected && (
                <div className="space-y-4 rounded-lg border p-4">
                    <div className="flex flex-wrap items-center gap-3">
                        <span className="text-sm text-muted-foreground">邀请码</span>
                        <code className="rounded bg-muted px-2 py-1 font-mono tracking-widest">
                            {selected.join_code}
                        </code>
                        <Button
                            size="sm"
                            variant="ghost"
                            disabled={busy}
                            title="换一张新码，旧码立即失效"
                            onClick={() =>
                                run(async () => {
                                    await classroomApi.rotateJoinCode(selected.id);
                                    await loadClassrooms();
                                }, '换码失败')
                            }
                        >
                            <RefreshCw className="mr-1 h-3 w-3" /> 换码
                        </Button>
                    </div>

                    <div className="space-y-2">
                        <Label htmlFor="new-assignment-title">布置新作业</Label>
                        <Input
                            id="new-assignment-title"
                            value={newAssignment.title}
                            placeholder="作业标题"
                            maxLength={120}
                            onChange={(event) =>
                                setNewAssignment((prev) => ({ ...prev, title: event.target.value }))
                            }
                        />
                        <Textarea
                            value={newAssignment.instructions}
                            placeholder="作业要求（可选）"
                            maxLength={8000}
                            onChange={(event) =>
                                setNewAssignment((prev) => ({
                                    ...prev,
                                    instructions: event.target.value,
                                }))
                            }
                        />
                        <Button
                            size="sm"
                            disabled={busy || !newAssignment.title.trim()}
                            onClick={() =>
                                run(async () => {
                                    await classroomApi.createAssignment(selected.id, {
                                        title: newAssignment.title.trim(),
                                        instructions: newAssignment.instructions,
                                        is_published: true,
                                    });
                                    setNewAssignment({ title: '', instructions: '' });
                                    setAssignments(
                                        await classroomApi.listClassroomAssignments(selected.id),
                                    );
                                }, '布置作业失败')
                            }
                        >
                            发布作业
                        </Button>
                    </div>

                    <ScrollArea className="max-h-56">
                        <ul className="space-y-2 pr-3">
                            {assignments.map((item) => (
                                <li
                                    key={item.id}
                                    className="flex items-center justify-between gap-2 rounded border p-2"
                                >
                                    <div className="min-w-0">
                                        <p className="truncate font-medium">{item.title}</p>
                                        <p className="text-xs text-muted-foreground">
                                            {item.is_published ? '已发布' : '草稿'} · 截止{' '}
                                            {formatDate(item.due_at)} · 已交 {item.submission_count ?? 0} /
                                            已批 {item.graded_count ?? 0}
                                        </p>
                                    </div>
                                    <div className="flex shrink-0 gap-1">
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={busy}
                                            onClick={() => void openSubmissions(item.id)}
                                        >
                                            批改
                                        </Button>
                                        <Button
                                            size="sm"
                                            variant="ghost"
                                            disabled={busy}
                                            onClick={() =>
                                                run(async () => {
                                                    await classroomApi.deleteAssignment(item.id);
                                                    setAssignments(
                                                        await classroomApi.listClassroomAssignments(
                                                            selected.id,
                                                        ),
                                                    );
                                                    if (openAssignmentId === item.id) {
                                                        setOpenAssignmentId(null);
                                                        setSubmissions([]);
                                                    }
                                                }, '删除失败')
                                            }
                                        >
                                            <Trash2 className="h-3 w-3" />
                                        </Button>
                                    </div>
                                </li>
                            ))}
                            {assignments.length === 0 && (
                                <li className="text-sm text-muted-foreground">还没有作业。</li>
                            )}
                        </ul>
                    </ScrollArea>

                    {openAssignment && (
                        <GradingList
                            // 换一份作业就整体重挂，草稿分数不会串到别的作业上。
                            key={openAssignment.id}
                            assignment={openAssignment}
                            submissions={submissions}
                            busy={busy}
                            onGrade={(studentId, score, feedback) =>
                                run(async () => {
                                    await classroomApi.grade(
                                        openAssignment.id,
                                        studentId,
                                        score,
                                        feedback,
                                    );
                                    setSubmissions(
                                        await classroomApi.listSubmissions(openAssignment.id),
                                    );
                                    setAssignments(
                                        await classroomApi.listClassroomAssignments(selected.id),
                                    );
                                }, '批改失败')
                            }
                        />
                    )}

                    <div>
                        <p className="mb-1 text-sm font-medium">学生完成情况</p>
                        <ul className="space-y-1 text-sm">
                            {progress.map((row) => (
                                <li key={row.student_id} className="flex justify-between gap-2">
                                    <span className="truncate">
                                        {row.display_name || row.username}
                                    </span>
                                    <span className="shrink-0 text-muted-foreground">
                                        {row.submitted_count}/{row.published_assignments} 已交 ·
                                        均分 {row.average_score ?? '—'}
                                        {row.late_count > 0 && ` · 迟交 ${row.late_count}`}
                                    </span>
                                </li>
                            ))}
                            {progress.length === 0 && (
                                <li className="text-muted-foreground">还没有学生加入。</li>
                            )}
                        </ul>
                    </div>
                </div>
            )}

            {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        </div>
    );
}

function GradingList({
    assignment,
    submissions,
    busy,
    onGrade,
}: {
    assignment: Assignment;
    submissions: Submission[];
    busy: boolean;
    onGrade: (studentId: string, score: number | null, feedback: string) => void;
}) {
    const [drafts, setDrafts] = useState<Record<string, { score: string; feedback: string }>>({});

    return (
        <div className="space-y-2 rounded border p-3">
            <p className="text-sm font-medium">
                《{assignment.title}》的提交（满分 {assignment.max_score}）
            </p>
            {submissions.length === 0 && (
                <p className="text-sm text-muted-foreground">还没有人提交。</p>
            )}
            {submissions.map((item) => {
                const draft = drafts[item.student_id] ?? {
                    score: item.score === null ? '' : String(item.score),
                    feedback: item.feedback,
                };
                return (
                    <div key={item.id} className="space-y-2 rounded bg-muted/40 p-2">
                        <p className="text-sm font-medium">
                            {item.student_display_name || item.student_username}
                            {item.is_late && (
                                <span className="ml-2 text-xs text-amber-600">迟交</span>
                            )}
                        </p>
                        <p className="whitespace-pre-wrap break-words text-sm">{item.content}</p>
                        <div className="flex flex-wrap items-end gap-2">
                            <div className="w-24 space-y-1">
                                <Label htmlFor={`score-${item.id}`} className="text-xs">
                                    分数
                                </Label>
                                <Input
                                    id={`score-${item.id}`}
                                    type="number"
                                    min={0}
                                    max={assignment.max_score}
                                    value={draft.score}
                                    onChange={(event) =>
                                        setDrafts((prev) => ({
                                            ...prev,
                                            [item.student_id]: {
                                                ...draft,
                                                score: event.target.value,
                                            },
                                        }))
                                    }
                                />
                            </div>
                            <div className="flex-1 min-w-[160px] space-y-1">
                                <Label htmlFor={`feedback-${item.id}`} className="text-xs">
                                    评语
                                </Label>
                                <Input
                                    id={`feedback-${item.id}`}
                                    value={draft.feedback}
                                    maxLength={4000}
                                    onChange={(event) =>
                                        setDrafts((prev) => ({
                                            ...prev,
                                            [item.student_id]: {
                                                ...draft,
                                                feedback: event.target.value,
                                            },
                                        }))
                                    }
                                />
                            </div>
                            <Button
                                size="sm"
                                disabled={busy}
                                onClick={() =>
                                    onGrade(
                                        item.student_id,
                                        draft.score.trim() === '' ? null : Number(draft.score),
                                        draft.feedback,
                                    )
                                }
                            >
                                保存评分
                            </Button>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}

// ----------------------------------------------------------------------
// 学生侧
// ----------------------------------------------------------------------
function StudentView() {
    const [classrooms, setClassrooms] = useState<Classroom[]>([]);
    const [assignments, setAssignments] = useState<StudentAssignment[]>([]);
    const [joinCode, setJoinCode] = useState('');
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [error, setError] = useState('');
    const [notice, setNotice] = useState('');
    const [busy, setBusy] = useState(false);

    const run = useCallback(async (action: () => Promise<void>, fallback: string) => {
        setBusy(true);
        setError('');
        try {
            await action();
        } catch (caught) {
            setError(readableApiError(caught, fallback));
        } finally {
            setBusy(false);
        }
    }, []);

    const reload = useCallback(async () => {
        const [enrolled, items] = await Promise.all([
            classroomApi.listEnrolled(),
            classroomApi.listMyAssignments(),
        ]);
        setClassrooms(enrolled);
        setAssignments(items);
    }, []);

    useEffect(() => {
        void run(reload, '无法加载班级');
    }, [run, reload]);

    return (
        <div className="space-y-4">
            {error && <p className="text-sm text-red-500" role="alert">{error}</p>}
            {notice && <p className="text-sm text-emerald-600">{notice}</p>}

            <div className="flex flex-wrap items-end gap-2">
                <div className="flex-1 min-w-[180px] space-y-1">
                    <Label htmlFor="join-code">邀请码</Label>
                    <Input
                        id="join-code"
                        value={joinCode}
                        placeholder="向老师索取 8 位邀请码"
                        maxLength={16}
                        onChange={(event) => setJoinCode(event.target.value)}
                    />
                </div>
                <Button
                    disabled={busy || !joinCode.trim()}
                    onClick={() =>
                        run(async () => {
                            const joined = await classroomApi.join(joinCode.trim());
                            setJoinCode('');
                            setNotice(`已加入「${joined.name}」`);
                            await reload();
                        }, '加入班级失败')
                    }
                >
                    加入班级
                </Button>
            </div>

            <div>
                <p className="mb-1 text-sm font-medium">我的班级</p>
                {classrooms.length === 0 ? (
                    <p className="text-sm text-muted-foreground">还没有加入任何班级。</p>
                ) : (
                    <ul className="space-y-1 text-sm">
                        {classrooms.map((item) => (
                            <li key={item.id} className="flex items-center justify-between gap-2">
                                <span className="truncate">
                                    {item.name}
                                    <span className="ml-2 text-muted-foreground">
                                        {item.teacher_display_name}
                                    </span>
                                </span>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    disabled={busy}
                                    onClick={() =>
                                        run(async () => {
                                            await classroomApi.leave(item.id);
                                            setNotice(`已退出「${item.name}」`);
                                            await reload();
                                        }, '退出失败')
                                    }
                                >
                                    退出
                                </Button>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            <div>
                <p className="mb-1 text-sm font-medium">我的作业</p>
                <ScrollArea className="max-h-72">
                    <ul className="space-y-3 pr-3">
                        {assignments.map((item) => {
                            const draft = drafts[item.id] ?? item.my_submission?.content ?? '';
                            return (
                                <li key={item.id} className="space-y-2 rounded border p-3">
                                    <div>
                                        <p className="font-medium">{item.title}</p>
                                        <p className="text-xs text-muted-foreground">
                                            {item.classroom_name} · 截止 {formatDate(item.due_at)} ·
                                            满分 {item.max_score}
                                        </p>
                                    </div>
                                    {item.instructions && (
                                        <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                                            {item.instructions}
                                        </p>
                                    )}

                                    {item.my_submission?.score !== null &&
                                        item.my_submission !== null && (
                                            <p className="text-sm">
                                                <span className="font-medium">
                                                    得分 {item.my_submission.score} /{' '}
                                                    {item.max_score}
                                                </span>
                                                {item.my_submission.feedback && (
                                                    <span className="ml-2 text-muted-foreground">
                                                        {item.my_submission.feedback}
                                                    </span>
                                                )}
                                            </p>
                                        )}
                                    {item.my_submission?.is_late && (
                                        <p className="text-xs text-amber-600">这次是迟交</p>
                                    )}

                                    <Textarea
                                        value={draft}
                                        maxLength={40000}
                                        placeholder="在这里写下你的答案"
                                        onChange={(event) =>
                                            setDrafts((prev) => ({
                                                ...prev,
                                                [item.id]: event.target.value,
                                            }))
                                        }
                                    />
                                    <Button
                                        size="sm"
                                        disabled={busy || !draft.trim()}
                                        onClick={() =>
                                            run(async () => {
                                                await classroomApi.submit(item.id, draft.trim());
                                                setNotice('已提交');
                                                await reload();
                                            }, '提交失败')
                                        }
                                        title={
                                            item.my_submission
                                                ? '重新提交会清空老师已给的分数'
                                                : undefined
                                        }
                                    >
                                        {item.my_submission ? '重新提交' : '提交'}
                                    </Button>
                                </li>
                            );
                        })}
                        {assignments.length === 0 && (
                            <li className="text-sm text-muted-foreground">暂时没有作业。</li>
                        )}
                    </ul>
                </ScrollArea>
            </div>

            {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />}
        </div>
    );
}
