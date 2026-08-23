import { CommonModule } from '@angular/common';
import { AfterViewInit, Component, ElementRef, Input, ViewChild } from '@angular/core';

/** Mouse/stylus/touch signature capture, shared by every place in the app that needs an
 * analyst's drawn signature (taint-analysis report export, per-transaction custody log).
 * Kept deliberately dumb: it only knows how to draw and hand back a PNG data URL - the
 * declaration checkbox, submit button and "what happens with the signature" text stay
 * with each caller, since that wording differs per use.
 */
@Component({
  selector: 'app-signature-pad',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './signature-pad.component.html',
  styleUrl: './signature-pad.component.scss',
})
export class SignaturePadComponent implements AfterViewInit {
  @Input() placeholder = 'Potpišite se ovde';

  @ViewChild('canvas') private canvasRef?: ElementRef<HTMLCanvasElement>;

  protected isDrawing = false;
  hasStrokes = false;

  ngAfterViewInit(): void {
    this.clear();
  }

  private context(): CanvasRenderingContext2D | null {
    const canvas = this.canvasRef?.nativeElement;
    return canvas ? canvas.getContext('2d') : null;
  }

  /** Pointer events rather than separate mouse/touch handlers, so drawing works with a
   * mouse, a trackpad and a stylus without three code paths. */
  startStroke(event: PointerEvent): void {
    const context = this.context();
    if (!context) {
      return;
    }
    this.isDrawing = true;
    const { x, y } = this.point(event);
    context.beginPath();
    context.moveTo(x, y);
    (event.target as HTMLCanvasElement).setPointerCapture(event.pointerId);
  }

  continueStroke(event: PointerEvent): void {
    const context = this.context();
    if (!this.isDrawing || !context) {
      return;
    }
    const { x, y } = this.point(event);
    context.lineTo(x, y);
    context.stroke();
    this.hasStrokes = true;
  }

  endStroke(): void {
    this.isDrawing = false;
  }

  private point(event: PointerEvent): { x: number; y: number } {
    const canvas = this.canvasRef!.nativeElement;
    const rect = canvas.getBoundingClientRect();
    // The canvas is drawn at a fixed internal resolution but laid out responsively, so
    // pointer coordinates have to be scaled or the ink lands away from the cursor.
    return {
      x: (event.clientX - rect.left) * (canvas.width / rect.width),
      y: (event.clientY - rect.top) * (canvas.height / rect.height),
    };
  }

  clear(): void {
    const canvas = this.canvasRef?.nativeElement;
    const context = this.context();
    if (!canvas || !context) {
      return;
    }
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.strokeStyle = '#0b1a33';
    context.lineWidth = 2.5;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    this.hasStrokes = false;
  }

  /** PNG data URL of whatever is currently drawn - callers only read this once the
   * declaration checkbox is ticked and `hasStrokes` is true. */
  getDataUrl(): string {
    return this.canvasRef!.nativeElement.toDataURL('image/png');
  }
}
