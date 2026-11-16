import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

/** The full-size image of an attached file, with its name. */
export default function ImageDialog({ id, filename, onClose }: { id: string; filename: string; onClose(): void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{filename}</DialogTitle>
        </DialogHeader>
        <img src={`/api/documents/${id}/image?size=full`} alt={filename} className="max-h-[75vh] w-full object-contain" />
      </DialogContent>
    </Dialog>
  )
}
