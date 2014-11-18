import os.path
import shutil
import subprocess
from fusesoc import utils

from fusesoc.build.backend import Backend
class Ise(Backend):

    TCL_FILE_TEMPLATE = """

project new {design}
project set family {family}
project set device {device}
project set package {package}
project set speed {speed}
project set "Generate Detailed MAP Report" true
project set "Verilog Include Directories" "{verilog_include_dirs}" -process "Synthesize - XST"
{source_files}
{global_includes}
{generated_srcs}
project set top "{top_module}"
"""

    TCL_FUNCTION_SYNTH = """
process run "Synthesize - XST"
"""
    TCL_FUNCTIONS_BUILD = """
process run "Generate Programming File"
"""


# @YLD:TODO fix hackiness:
# * support lang as verilog or vhdl?
# * should some of these should be configurable, such as toplevel?
# * take system module name from config (system_i in this case)?
# * determine system.mhs filename from config also?
    PLATGEN_CMD_TEMPLATE = """
-p {device}{package}{speed} -lang verilog -intstyle pa -toplevel no -ti system_i -msg __xps/ise/xmsgprops.lst system.mhs
"""
# @YLD:TODO - does output filename matter? Required for next steps?
    SYNTH_CMD_TEMPLATE = """
-ifn {xst_script} -ofn parallella_z7_top.srp -intstyle ise
"""
    TCL_FILE2_TEMPLATE = """
project open {design}

"""

# @YLD:TODO - think vlgincdir is plain wrong.
    XST_FILE_TEMPLATE = """
set -loop_iteration_limit 1000
run
-ifn parallella_z7_top.prj
-top parallella_z7_top
-p  {device}{package}{speed}-1
-ofn parallella_z7_top.ngc
-vlgincdir {../src/parallella/data}
-sd \{implementation\}
-opt_mode speed
-opt_level 1
-netlist_hierarchy rebuilt
"""

    TOOL_NAME = 'ise'

    def __init__(self, system):
        super(Ise, self).__init__(system)
        self.src_files       += [os.path.join(self.src_root, self.system.name, f) for f in self.system.backend.ucf_files]
        self.global_includes += [os.path.join(self.src_root, self.system.name, f) for f in self.system.backend.global_includes]
        self.work_root = os.path.join(self.build_root, 'bld-'+self.TOOL_NAME)

    def configure(self):
        super(Ise, self).configure()
        src_dir = self.system.system_root
        dst_dir = os.path.join(self.src_root, self.system.name)

        export_files = self.system.backend.export()
        dirs = list(set(map(os.path.dirname, export_files)))

        for d in dirs:
            if not os.path.exists(os.path.join(dst_dir, d)):
                os.makedirs(os.path.join(dst_dir, d))

        for f in export_files:
            if(os.path.exists(os.path.join(src_dir, f))):
                shutil.copyfile(os.path.join(src_dir, f),
                                os.path.join(dst_dir, f))
            else:
                utils.pr_warn("File " + os.path.join(src_dir, f) + " doesn't exist")

        self._write_tcl_file()

    def _write_tcl_file(self):
        tcl_file = open(os.path.join(self.work_root, self.system.name+'.tcl'),'w')
        tcl_file.write(self.TCL_FILE_TEMPLATE.format(
            design               = self.system.name,
            family               = self.system.backend.family,
            device               = self.system.backend.device,
            package              = self.system.backend.package,
            speed                = self.system.backend.speed,
            top_module           = self.system.backend.top_module,
            verilog_include_dirs = '|'.join(self.include_dirs),
            source_files         = '\n'.join(['xfile add '+s for s in self.src_files]),
            global_includes      = '\n'.join(['xfile add '+s+' -include_global' for s in self.global_includes]),
            generated_srcs       = '\n'.join(['xfile add '+s for s in self.system.backend.generated_srcs])))

        for f in self.system.backend.tcl_files:
            tcl_file.write(open(os.path.join(self.system_root, f)).read())

        # TODO - put a sensible variable in config!
        if self.system.backend.system_files:
            tcl_file.write(self.TCL_FUNCTION_SYNTH)
        else:
            tcl_file.write(self.TCL_FUNCTIONS_BUILD)
        tcl_file.close()

        # Part of Parallella workaround - hopefully temporary - split in twain
        if self.system.backend.system_files:
            tcl_file2 = open(os.path.join(self.work_root, self.system.name+'2.tcl'),'w')
            tcl_file2.write(self.TCL_FILE2_TEMPLATE.format(
                design               = self.system.name))
            tcl_file2.write(self.TCL_FUNCTIONS_BUILD)
            tcl_file2.close()

            xst_file = open(os.path.join(self.work_root, self.system.name+'-custom.xst'),'w')
            xst_file.write(self.XST_FILE_TEMPLATE.format(
                device               = self.system.backend.device,
                package              = self.system.backend.package,
                speed                = self.system.backend.speed))
 
        self.extrasynth()

    def build(self, args):
        super(Ise, self).build(args)

        if self.system.backend.system_files:
            self.platgen()


        utils.Launcher('xtclsh', [os.path.join(self.work_root, self.system.name+'.tcl')],
                           cwd = self.work_root,
                           errormsg = "Failed to make FPGA load module").run()

        # Parallella workaround
        if self.system.backend.xst_script:
                self.extrasynth()
                utils.Launcher('xtclsh', [os.path.join(self.work_root, self.system.name+'2.tcl')],
                           cwd = self.work_root,
                           errormsg = "Failed to make FPGA load module").run()

        super(Ise, self).done()

    def pgm(self, remaining):
        pass

    # This is a bit hacky - not sure how specific to Parallella it might be at this point
    # Copy sources into place, platgen, xst, copy outputs to toplevel folder
    def platgen(self):
        cmdline = []
        cmdline = self.PLATGEN_CMD_TEMPLATE.format(
            device               = self.system.backend.device,
            package              = self.system.backend.package,
            speed                = self.system.backend.speed).split()
        
        sys_files = self.system.backend.system_files

        # NOTE - a bit hacky. Copy from src dir, based on common path
        # might be better if we looked at extension, or allowed config to determine roles?
        commondir = os.path.commonprefix(sys_files)
        src_dir = os.path.join(self.src_root, self.system.name)
        dirs = list(set(map(os.path.dirname, sys_files)))
        for d in dirs:
            if not os.path.exists(os.path.join(self.work_root, os.path.relpath(d, commondir))):
                os.makedirs(os.path.join(self.work_root,       os.path.relpath(d, commondir)))

        for f in sys_files:
            shutil.copyfile(os.path.join(src_dir,        f),
                            os.path.join(self.work_root, os.path.relpath(f, commondir)))

        utils.Launcher('platgen', cmdline,
                       cwd = self.work_root,
                       errormsg = "Failed to run %s" % cmdline).run()
        utils.Launcher('xst', ["-ifn","system_xst.scr"],
                       cwd = os.path.join(self.work_root, "synthesis"),
                       errormsg = "Failed to run %s" % cmdline).run()

        for f in os.listdir(os.path.join(self.work_root, "implementation")):
            if f.endswith('.ngc'):
                shutil.copyfile(os.path.join(self.work_root, "implementation", f),
                                os.path.join(self.work_root, f))

    # Workaround for Parallella, hopefully temporary. xtclsh otherwise generates own script and uses it, which is no good.
    def extrasynth(self):
        cmdline = []
        script = os.path.join(self.src_root, self.system.backend.xst_script)
        utils.pr_warn("File " + script)
        cmdline = self.SYNTH_CMD_TEMPLATE.format(
            xst_script           = script).split()
        utils.pr_warn("Cmd " + cmdline.join(' '))
 
        utils.Launcher('xst', cmdline,
                       cwd = self.work_root,
                       errormsg = "Failed to run %s" % cmdline).run()

