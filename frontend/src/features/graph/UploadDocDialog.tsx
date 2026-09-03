import { useState, useRef } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { SteppedSlider } from "../../components/ui/stepped-slider";
import { Loader2, Upload, FileText, CheckCircle2 } from "lucide-react";

interface UploadDocDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  complexity: number;
  setComplexity: (value: number) => void;
  isUploading: boolean;
  isGenerating: boolean;
  onUploadAndGenerate: (file: File, complexity: number) => void;
  t: (key: string) => string;
}

/**
 * 文档上传对话框
 *
 * NOTE: 支持拖拽和点击两种上传方式，上传后自动触发星图生成
 */
export function UploadDocDialog({
  open,
  onOpenChange,
  complexity,
  setComplexity,
  isUploading,
  isGenerating,
  onUploadAndGenerate,
  t
}: UploadDocDialogProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isProcessing = isUploading || isGenerating;

  const complexitySteps = [
    t('app.complexity_simple'),
    t('app.complexity_standard'),
    t('app.complexity_detailed'),
  ];

  const handleFileSelect = (file: File) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      return;
    }
    setSelectedFile(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFileSelect(file);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFileSelect(file);
  };

  const handleSubmit = () => {
    if (selectedFile) {
      onUploadAndGenerate(selectedFile, complexity);
    }
  };

  /** 对话框关闭时清理状态 */
  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen && !isProcessing) {
      setSelectedFile(null);
      setDragOver(false);
    }
    onOpenChange(isOpen);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>{t('doc.dialog_title')}</DialogTitle>
          <DialogDescription>
            {t('doc.dialog_desc')}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-6 py-4">
          {/* 文件拖拽上传区域 */}
          <div
            className={`
              relative flex flex-col items-center justify-center
              rounded-lg border-2 border-dashed p-8 transition-all cursor-pointer
              ${dragOver
                ? 'border-primary bg-primary/10'
                : selectedFile
                  ? 'border-success bg-success/10'
                  : 'border-foreground/25 hover:border-foreground/45 hover:bg-foreground/5'
              }
              ${isProcessing ? 'pointer-events-none opacity-60' : ''}
            `}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              onChange={handleInputChange}
              className="hidden"
              disabled={isProcessing}
            />

            {selectedFile ? (
              <>
                <CheckCircle2 className="mb-3 h-10 w-10 text-success" />
                <div className="flex items-center gap-2 text-sm text-foreground">
                  <FileText className="h-4 w-4" />
                  <span className="font-medium">{selectedFile.name}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                </p>
                <p className="mt-2 text-xs text-muted-foreground">
                  {t('doc.click_to_change')}
                </p>
              </>
            ) : (
              <>
                <Upload className="mb-3 h-10 w-10 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  {t('doc.drop_hint')}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {t('doc.file_limit')}
                </p>
              </>
            )}
          </div>

          {/* 知识深度滑块 */}
          <div className="grid grid-cols-4 items-center gap-4">
            <label className="text-right text-sm font-medium text-foreground">
              {t('app.complexity_label')}
            </label>
            <div className="col-span-3 px-1">
              <SteppedSlider
                value={complexity}
                onChange={setComplexity}
                steps={complexitySteps}
                disabled={isProcessing}
              />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            onClick={handleSubmit}
            disabled={isProcessing || !selectedFile}
            className="w-full"
          >
            {isProcessing ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {isUploading ? t('doc.uploading') : t('doc.generating_graph')}
              </>
            ) : (
              t('doc.start_analyze')
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
