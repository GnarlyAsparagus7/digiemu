// Every reference INTO an address range, with the reading instruction and the
// function it sits in. Finds the fields of a DMA'd structure that code touches.
//   tools/ghidra.sh run RefsInRange.java <outfile> <lohex> <hihex>
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import java.io.PrintWriter;

public class RefsInRange extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        PrintWriter w = new PrintWriter(args[0]);
        long lo = Long.decode(args[1]), hi = Long.decode(args[2]);
        int n = 0;
        ReferenceIterator it =
            currentProgram.getReferenceManager().getReferenceIterator(
                currentProgram.getMinAddress());
        while (it.hasNext()) {
            Reference r = it.next();
            Address to = r.getToAddress();
            if (to == null) continue;
            long t = to.getOffset();
            if (t < lo || t >= hi) continue;
            Address from = r.getFromAddress();
            Instruction ins = getInstructionAt(from);
            Function f = getFunctionContaining(from);
            w.println(String.format("+0x%03x  %s  from %s  fn %-10s  %-8s %s",
                t - lo, to, from,
                f == null ? "?" : f.getEntryPoint().toString(),
                r.getReferenceType(),
                ins == null ? "(data)" : ins.toString()));
            n++;
        }
        w.println("# " + n + " references into [" + args[1] + "," + args[2] + ")");
        w.close();
        println("wrote " + args[0] + " (" + n + " refs)");
    }
}
