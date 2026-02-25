declare module 'qrcode-terminal' {
  function generate(text: string, options?: { small?: boolean }): void;
  export = { generate };
}

declare module 'pdf-parse' {
  interface PDFData {
    numpages: number;
    numrender: number;
    info: Record<string, any>;
    metadata: any;
    text: string;
    version: string;
  }
  function pdfParse(buffer: Buffer): Promise<PDFData>;
  export default pdfParse;
}
