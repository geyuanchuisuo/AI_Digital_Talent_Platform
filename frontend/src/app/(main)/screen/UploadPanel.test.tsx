import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UploadPanel from './UploadPanel';
import { requeueTask, uploadBatch, fetchUploadOptions } from './api';
import type { BatchUploadData } from './types';

vi.mock('./api', () => ({
  uploadBatch: vi.fn(),
  requeueTask: vi.fn(),
  fetchUploadOptions: vi.fn(),
}));

const uploadBatchMock = vi.mocked(uploadBatch);
const requeueTaskMock = vi.mocked(requeueTask);

const RESULT: BatchUploadData = {
  upload_batch_id: '01JABCDEFGHJKMNPQRSTVWXYZA',
  accepted: 1,
  duplicates: [
    {
      filename: '李四.pdf',
      reason: '文件内容已存在',
      resume_file_id: 9,
      task_id: 12,
      task_status: 'failed',
      can_requeue: true,
    },
    {
      filename: '王五.pdf',
      reason: '文件内容已存在',
      resume_file_id: 10,
      task_id: 13,
      task_status: 'success',
      can_requeue: false,
    },
  ],
  rejected: [{ filename: 'photo.exe', reason: '非法文件类型，仅支持 docx/pdf' }],
  queue_total: 3,
};

function selectFile(container: HTMLElement, name = '张三.pdf'): void {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(['dummy'], name, { type: 'application/pdf' });
  fireEvent.change(input, { target: { files: [file] } });
}

describe('UploadPanel', () => {
  beforeEach(() => {
    vi.mocked(fetchUploadOptions).mockResolvedValue({allowed_ext:['pdf','docx','png','jpg'], max_mb:12, ocr_enabled:true});
    uploadBatchMock.mockReset();
    requeueTaskMock.mockReset();
  });

  it('uses image formats and upload limits supplied by the backend', async () => {
    const {container}=render(<UploadPanel onChanged={vi.fn()} />);
    await waitFor(() => expect(container.querySelector('input[type="file"]')?.getAttribute('accept')).toContain('.png'));
    expect(screen.getByText(/12MB/)).toBeTruthy();
  });

  it('上传成功展示批次号、重复项与拒绝项，可对失败原任务重新入队', async () => {
    uploadBatchMock.mockResolvedValue(RESULT);
    requeueTaskMock.mockResolvedValue({
      task_id: 12,
      status: 'pending',
      task_status: 'waiting',
      stage: null,
      retries: 1,
    });
    const onChanged = vi.fn();

    const { container } = render(<UploadPanel onChanged={onChanged} />);
    selectFile(container);
    // rc-upload 的文件列表更新在微任务里，等它进 state 再点提交
    await waitFor(() => expect(screen.getByText('已选 1 个文件')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: '上传并解析' }));

    await waitFor(() => expect(uploadBatchMock).toHaveBeenCalledTimes(1));
    const files = uploadBatchMock.mock.calls[0][0];
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe('张三.pdf');

    expect(await screen.findByText('批次 01JABCDE…XYZA')).toBeTruthy();
    expect(screen.getByText('本批次新入队 1 份')).toBeTruthy();
    expect(screen.getByText('李四.pdf')).toBeTruthy();
    expect(screen.getByText('王五.pdf')).toBeTruthy();
    expect(screen.getByText('photo.exe')).toBeTruthy();
    expect(onChanged).toHaveBeenCalledTimes(1);

    // 只有 can_requeue=true 的重复项有按钮
    const requeueButtons = screen.getAllByRole('button', { name: '重新入队' });
    expect(requeueButtons).toHaveLength(1);

    fireEvent.click(requeueButtons[0]);
    await waitFor(() => expect(requeueTaskMock).toHaveBeenCalledWith(12));
    expect(await screen.findByText('已重新入队')).toBeTruthy();
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it('未选择文件时提示且不发起请求', () => {
    const onChanged = vi.fn();
    render(<UploadPanel onChanged={onChanged} />);

    fireEvent.click(screen.getByRole('button', { name: '上传并解析' }));

    expect(uploadBatchMock).not.toHaveBeenCalled();
    expect(onChanged).not.toHaveBeenCalled();
  });
});
