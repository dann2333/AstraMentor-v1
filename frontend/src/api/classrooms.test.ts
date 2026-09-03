import { describe, expect, it, vi } from 'vitest';
import { client } from './client';
import { classroomApi } from './classrooms';

describe('classroom api', () => {
    it('joins with the code in a body, never a query string', async () => {
        const post = vi.spyOn(client, 'post').mockResolvedValue({ data: { id: 'c1' } });
        await classroomApi.join('ABCD2345');
        // 邀请码走请求体，避免落进服务端与代理的访问日志
        expect(post).toHaveBeenCalledWith('/classrooms/join', { join_code: 'ABCD2345' });
    });

    it('submits only content and session id — no score field exists to smuggle', async () => {
        const put = vi.spyOn(client, 'put').mockResolvedValue({ data: {} });
        await classroomApi.submit('a1', '我的答案');
        expect(put).toHaveBeenCalledWith('/me/assignments/a1/submission', {
            content: '我的答案',
            session_id: null,
        });
    });

    it('grades through the teacher-only route', async () => {
        const put = vi.spyOn(client, 'put').mockResolvedValue({ data: {} });
        await classroomApi.grade('a1', 'student-1', 88, '不错');
        expect(put).toHaveBeenCalledWith('/assignments/a1/submissions/student-1/grade', {
            score: 88,
            feedback: '不错',
        });
    });

    it('can withdraw a score by sending null', async () => {
        const put = vi.spyOn(client, 'put').mockResolvedValue({ data: {} });
        await classroomApi.grade('a1', 'student-1', null, '请重做');
        expect(put).toHaveBeenCalledWith('/assignments/a1/submissions/student-1/grade', {
            score: null,
            feedback: '请重做',
        });
    });

    it('reads teacher and student class lists from different endpoints', async () => {
        const get = vi.spyOn(client, 'get').mockResolvedValue({ data: { classrooms: [] } });
        await classroomApi.listTaught();
        await classroomApi.listEnrolled();
        expect(get).toHaveBeenNthCalledWith(1, '/classrooms/taught');
        expect(get).toHaveBeenNthCalledWith(2, '/classrooms/enrolled');
    });
});
