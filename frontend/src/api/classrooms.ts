/** 班级与作业接口。这些接口一律需要登录，未登录时后端返回 401。 */
import { client } from './client';

export interface Classroom {
    id: string;
    teacher_id: string;
    teacher_display_name: string;
    name: string;
    description: string;
    is_archived: boolean;
    member_count: number;
    created_at: string;
    updated_at: string;
    /** 只有本班老师拿得到；学生视图里这个字段不存在。 */
    join_code?: string;
}

export interface ClassroomMember {
    student_id: string;
    username: string;
    display_name: string;
    joined_at: string;
}

export interface StudentProgress {
    student_id: string;
    username: string;
    display_name: string;
    joined_at: string;
    published_assignments: number;
    submitted_count: number;
    graded_count: number;
    late_count: number;
    average_score: number | null;
}

export type AssignmentTargetKind = 'free' | 'topic' | 'course' | 'node' | 'document';

export interface Assignment {
    id: string;
    classroom_id: string;
    classroom_name: string;
    title: string;
    instructions: string;
    target_kind: AssignmentTargetKind;
    target_topic: string;
    target_course_id: string | null;
    target_node: string;
    due_at: string | null;
    max_score: number;
    is_published: boolean;
    created_at: string;
    updated_at: string;
    /** 只在老师视图里返回。 */
    submission_count?: number;
    graded_count?: number;
}

export interface Submission {
    id: string;
    assignment_id: string;
    student_id: string;
    content: string;
    session_id: string | null;
    status: 'submitted' | 'graded';
    is_late: boolean;
    submitted_at: string;
    score: number | null;
    feedback: string;
    graded_by: string | null;
    graded_at: string | null;
    created_at: string;
    updated_at: string;
    student_username?: string;
    student_display_name?: string;
}

export interface StudentAssignment extends Assignment {
    my_submission: Submission | null;
}

export interface CreateAssignmentPayload {
    title: string;
    instructions?: string;
    target_kind?: AssignmentTargetKind;
    target_topic?: string;
    target_course_id?: string | null;
    target_node?: string;
    due_at?: string | null;
    max_score?: number;
    is_published?: boolean;
}

export const classroomApi = {
    // ---- 老师 ----
    createClassroom: async (name: string, description = '') => {
        const response = await client.post<Classroom>('/classrooms', { name, description });
        return response.data;
    },

    listTaught: async () => {
        const response = await client.get<{ classrooms: Classroom[] }>('/classrooms/taught');
        return response.data.classrooms;
    },

    updateClassroom: async (
        classroomId: string,
        changes: { name?: string; description?: string; is_archived?: boolean },
    ) => {
        const response = await client.patch<Classroom>(`/classrooms/${classroomId}`, changes);
        return response.data;
    },

    deleteClassroom: async (classroomId: string) => {
        await client.delete(`/classrooms/${classroomId}`);
    },

    rotateJoinCode: async (classroomId: string) => {
        const response = await client.post<Classroom>(
            `/classrooms/${classroomId}/join-code/rotate`,
        );
        return response.data;
    },

    listMembers: async (classroomId: string) => {
        const response = await client.get<{ members: ClassroomMember[] }>(
            `/classrooms/${classroomId}/members`,
        );
        return response.data.members;
    },

    removeMember: async (classroomId: string, studentId: string) => {
        await client.delete(`/classrooms/${classroomId}/members/${studentId}`);
    },

    classroomProgress: async (classroomId: string) => {
        const response = await client.get<{ students: StudentProgress[] }>(
            `/classrooms/${classroomId}/progress`,
        );
        return response.data.students;
    },

    listClassroomAssignments: async (classroomId: string) => {
        const response = await client.get<{ assignments: Assignment[] }>(
            `/classrooms/${classroomId}/assignments`,
        );
        return response.data.assignments;
    },

    createAssignment: async (classroomId: string, payload: CreateAssignmentPayload) => {
        const response = await client.post<Assignment>(
            `/classrooms/${classroomId}/assignments`,
            payload,
        );
        return response.data;
    },

    updateAssignment: async (
        assignmentId: string,
        changes: Partial<CreateAssignmentPayload> & { clear_due_at?: boolean },
    ) => {
        const response = await client.patch<Assignment>(`/assignments/${assignmentId}`, changes);
        return response.data;
    },

    deleteAssignment: async (assignmentId: string) => {
        await client.delete(`/assignments/${assignmentId}`);
    },

    listSubmissions: async (assignmentId: string) => {
        const response = await client.get<{ submissions: Submission[] }>(
            `/assignments/${assignmentId}/submissions`,
        );
        return response.data.submissions;
    },

    grade: async (
        assignmentId: string,
        studentId: string,
        score: number | null,
        feedback = '',
    ) => {
        const response = await client.put<Submission>(
            `/assignments/${assignmentId}/submissions/${studentId}/grade`,
            { score, feedback },
        );
        return response.data;
    },

    // ---- 学生 ----
    listEnrolled: async () => {
        const response = await client.get<{ classrooms: Classroom[] }>('/classrooms/enrolled');
        return response.data.classrooms;
    },

    join: async (joinCode: string) => {
        const response = await client.post<Classroom>('/classrooms/join', {
            join_code: joinCode,
        });
        return response.data;
    },

    leave: async (classroomId: string) => {
        await client.post(`/classrooms/${classroomId}/leave`);
    },

    listMyAssignments: async () => {
        const response = await client.get<{ assignments: StudentAssignment[] }>(
            '/me/assignments',
        );
        return response.data.assignments;
    },

    getMyAssignment: async (assignmentId: string) => {
        const response = await client.get<StudentAssignment>(`/me/assignments/${assignmentId}`);
        return response.data;
    },

    submit: async (assignmentId: string, content: string, sessionId?: string) => {
        const response = await client.put<Submission>(
            `/me/assignments/${assignmentId}/submission`,
            { content, session_id: sessionId ?? null },
        );
        return response.data;
    },
};
