// Dump the disassembly listing for an address range, with operand references.
//   tools/ghidra.sh run Listing.java <outfile> <lohex> <hihex>
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.address.AddressSet;
import java.io.PrintWriter;

public class Listing extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        PrintWriter w = new PrintWriter(args[0]);
        Address lo = toAddr(Long.decode(args[1])), hi = toAddr(Long.decode(args[2]));
        InstructionIterator it = currentProgram.getListing()
                .getInstructions(new AddressSet(lo, hi), true);
        int n = 0;
        while (it.hasNext()) {
            Instruction ins = it.next();
            StringBuilder sb = new StringBuilder();
            sb.append(String.format("%s  %-40s", ins.getAddress(), ins.toString()));
            Address[] flows = ins.getFlows();
            if (flows.length > 0) {
                sb.append(" ->");
                for (Address f : flows) sb.append(" ").append(f);
            }
            w.println(sb.toString());
            n++;
        }
        w.println("# " + n + " instructions");
        w.close();
        println("wrote " + args[0] + " (" + n + " instructions)");
    }
}
