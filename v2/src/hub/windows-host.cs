using System;
using System.IO;
using System.Text;
using System.Linq;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace NiraiLocal {
  public class Change { public string path; public string before_sha256; public string content; }
  public class Source { public string path, sha256; }
  public class Config {
    public string kind, root, run_id, executable, executable_sha256;
    public string[] argv, protected_roots;
    public Change[] changes;
    public Source[] sources;
    public Dictionary<string,string> env;
    public int timeout_ms, output_bytes, parent_pid;
  }
  public static class Host {
    static volatile bool cancelled;
    static string dir;
    static readonly JavaScriptSerializer json = new JavaScriptSerializer { MaxJsonLength = 4 * 1024 * 1024 };
    static Dictionary<string,object> Obj(params object[] fields) {
      var d = new Dictionary<string,object>();
      for (int i=0;i<fields.Length;i+=2) d.Add((string)fields[i],fields[i+1]);
      return d;
    }
    public static string Hash(byte[] data) { using(var h=SHA256.Create()) return BitConverter.ToString(h.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
    static void Save(string name, object value) {
      string file=Path.Combine(dir,name), temp=file+".tmp";
      byte[] bytes=Encoding.UTF8.GetBytes(json.Serialize(value));
      using(var s=new FileStream(temp,FileMode.Create,FileAccess.Write,FileShare.None)) { s.Write(bytes,0,bytes.Length); s.Flush(true); }
      if(File.Exists(file)) File.Replace(temp,file,null); else File.Move(temp,file);
    }
    static Dictionary<string,object> Result(string state,string effects,string cleanup,object value, string error=null) {
      return Obj("state",state,"effects",effects,"cleanup_state",cleanup,"result",value,"error",error==null?null:Obj("message",error));
    }
    public static void Run(string path) {
      dir=Path.GetDirectoryName(Path.GetFullPath(path));
      Task.Run(()=> { try { Console.ReadLine(); } finally { cancelled=true; } });
      try {
        var c=json.Deserialize<Config>(File.ReadAllText(path));
        if(c.parent_pid>0) {
          try {
            var parent=Process.GetProcessById(c.parent_pid); var handle=parent.Handle;
            Task.Run(()=> { try { parent.WaitForExit(); } finally { cancelled=true;parent.Dispose(); } });
          } catch { cancelled=true; }
        }
        using(var me=Process.GetCurrentProcess()) Save("host.json",Obj("pid",me.Id,"creation_time",me.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(),"run_id",c.run_id));
        var result=c.kind=="patch"?Patch(c):Command(c);
        Save("result.json",result);
      } catch(Exception error) {
        // Never echo request content, environment or tokens in diagnostic output.
        Save("diagnostic.json",Obj("type",error.GetType().Name,"code",error.HResult,"stack",error.StackTrace));
        Save("result.json",Result("Failed",File.Exists(Path.Combine(dir,"journal.json"))?"unknown":"none",
          File.Exists(Path.Combine(dir,"journal.json"))?"unknown":"clear",null,"Local worker failed; inspect the saved journal"));
      }
    }
    public static string Recover(string path) {
      string folder=Path.GetDirectoryName(path);
      if(!File.Exists(Path.Combine(folder,"host.json"))) return "unknown";
      var host=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(Path.Combine(folder,"host.json")));
      try { using(var p=Process.GetProcessById(Convert.ToInt32(host["pid"]))) {
        if(p.StartTime.ToUniversalTime().ToFileTimeUtc().ToString()==(string)host["creation_time"] && !p.HasExited) return "active";
      } } catch(ArgumentException) {} catch(InvalidOperationException) {}
      string run=(string)host["run_id"];
      IntPtr job=Native.OpenJobObject(0x000C,false,"Local\\Nirai-v2-run-"+run);
      if(job==IntPtr.Zero) return Marshal.GetLastWin32Error()==2?"stopped":"unknown";
      try {
        if(!Native.TerminateJobObject(job,1)) return "unknown";
        var watch=Stopwatch.StartNew();
        while(watch.ElapsedMilliseconds<5000) {
          Native.Accounting info;
          if(!Native.QueryInformationJobObject(job,1,out info,Marshal.SizeOf(typeof(Native.Accounting)),IntPtr.Zero)) return "unknown";
          if(info.active==0) return "stopped";
          Thread.Sleep(20);
        }
        return "unknown";
      } finally { Native.CloseHandle(job); }
    }
    static string Target(Config c, string path) {
      if(Path.IsPathRooted(path) || path.IndexOf(':')>=0) throw new IOException("relative path required");
      string root=Path.GetFullPath(c.root).TrimEnd('\\')+"\\", target=Path.GetFullPath(Path.Combine(root,path));
      if(!target.StartsWith(root,StringComparison.OrdinalIgnoreCase)) throw new IOException("outside scope");
      foreach(string p in c.protected_roots ?? new string[0]) {
        string protectedPath=Path.GetFullPath(p).TrimEnd('\\');
        if(target.Equals(protectedPath,StringComparison.OrdinalIgnoreCase) || target.StartsWith(protectedPath+"\\",StringComparison.OrdinalIgnoreCase)) throw new IOException("protected path");
      }
      return target;
    }
    static List<SafeFileHandle> LockParents(IEnumerable<string> targets) {
      var paths=new SortedSet<string>(StringComparer.OrdinalIgnoreCase);
      foreach(string target in targets) {
        string parent=Path.GetDirectoryName(target);
        while(!String.IsNullOrEmpty(parent)) { paths.Add(parent); parent=Path.GetDirectoryName(parent); }
      }
      var handles=new List<SafeFileHandle>();
      try {
        foreach(string p in paths) {
          var h=Native.CreateFile(p,0,3,IntPtr.Zero,3,0x02200000,IntPtr.Zero);
          if(h.IsInvalid) throw new Win32Exception();
          handles.Add(h);
          Native.FileInfo info;
          if(!Native.GetFileInformationByHandle(h,out info) || (info.attributes&0x400)!=0 || (info.attributes&0x10)==0) throw new IOException("linked parent");
        }
        return handles;
      } catch { foreach(var h in handles) h.Dispose(); throw; }
    }
    static byte[] Read(FileStream file) {
      if(file.Length>1024*1024) throw new IOException("patch source exceeds limit");
      file.Position=0; byte[] data=new byte[(int)file.Length]; int offset=0, count;
      while(offset<data.Length && (count=file.Read(data,offset,data.Length-offset))>0) offset+=count;
      if(offset!=data.Length) throw new IOException("incomplete source");
      return data;
    }
    static void Write(FileStream file,byte[] data) { file.Position=0; file.Write(data,0,data.Length); file.SetLength(data.Length); file.Flush(true); }
    static FileStream OpenPatch(string path,bool create) {
      var h=Native.CreateFile(path,0xC0010000,1,IntPtr.Zero,create?1u:3u,0x00200000,IntPtr.Zero);
      if(h.IsInvalid) { h.Dispose(); throw new Win32Exception(); }
      Native.FileInfo info;
      if(!Native.GetFileInformationByHandle(h,out info) || info.links!=1 || (info.attributes&0x400)!=0) { h.Dispose();throw new IOException("shared or linked file"); }
      return new FileStream(h,FileAccess.ReadWrite);
    }
    static Dictionary<string,object> Patch(Config c) {
      string[] paths=c.changes.Select(x=>Target(c,x.path)).ToArray();
      var parents=LockParents(paths);
      var opened=new FileStream[paths.Length]; var originals=new byte[paths.Length][];
      var entries=new List<Dictionary<string,object>>(); var touched=new List<int>();
      bool began=false, restored=true;
      try {
        // Hold existing files against writes/deletion for the entire apply/rollback.
        for(int i=0;i<paths.Length;i++) {
          Change ch=c.changes[i];
          if(ch.before_sha256==null) { if(File.Exists(paths[i]) || Directory.Exists(paths[i])) throw new IOException("new path already exists"); }
          else {
            if((File.GetAttributes(paths[i])&FileAttributes.ReparsePoint)!=0) throw new IOException("linked file");
            opened[i]=OpenPatch(paths[i],false);
            Native.FileInfo info;
            if(!Native.GetFileInformationByHandle(opened[i].SafeFileHandle,out info) || info.links!=1 || (info.attributes&0x400)!=0) throw new IOException("shared or linked file");
            originals[i]=Read(opened[i]);
            if(Hash(originals[i])!=ch.before_sha256) throw new IOException("before fingerprint changed");
            using(var backup=new FileStream(Path.Combine(dir,i+".before"),FileMode.CreateNew,FileAccess.Write,FileShare.Read)) {
              backup.Write(originals[i],0,originals[i].Length); backup.Flush(true);
            }
          }
          entries.Add(Obj("path",ch.path,"before_sha256",ch.before_sha256,"after_sha256",Hash(Encoding.UTF8.GetBytes(ch.content)),"phase","prepared","backup",ch.before_sha256==null?null:i+".before"));
        }
        Save("journal.json",Obj("kind","patch","run_id",c.run_id,"root",c.root,"entries",entries)); began=true;
        for(int i=0;i<paths.Length;i++) {
          if(cancelled) throw new OperationCanceledException();
          entries[i]["phase"]="writing"; Save("journal.json",Obj("kind","patch","run_id",c.run_id,"root",c.root,"entries",entries));
          if(opened[i]==null) opened[i]=OpenPatch(paths[i],true);
          touched.Add(i);
          Write(opened[i],Encoding.UTF8.GetBytes(c.changes[i].content));
          entries[i]["phase"]="applied"; Save("journal.json",Obj("kind","patch","run_id",c.run_id,"root",c.root,"entries",entries));
        }
        if(cancelled) throw new OperationCanceledException();
        return Result("Completed","applied","clear",Obj("changes",entries,"summary",paths.Length+" files patched"));
      } catch(Exception error) {
        // Existing file handles exclude other writers. Recheck our exact result
        // before rollback anyway; never overwrite a file that no longer matches.
        foreach(int i in touched.AsEnumerable().Reverse()) {
          try {
            byte[] current=Read(opened[i]);
            if(Hash(current)!=(string)entries[i]["after_sha256"]) { restored=false; entries[i]["phase"]="partial"; continue; }
            if(originals[i]==null) {
              // Delete by the same handle, avoiding a close/reopen replacement race.
              if(!Native.MarkDelete(opened[i].SafeFileHandle)) { restored=false; entries[i]["phase"]="partial"; continue; }
            } else Write(opened[i],originals[i]);
            entries[i]["phase"]="restored";
          } catch { restored=false; entries[i]["phase"]="partial"; }
        }
        if(began) Save("journal.json",Obj("kind","patch","run_id",c.run_id,"root",c.root,"entries",entries));
        return Result(error is OperationCanceledException?"Cancelled":"Failed",restored?"none":"partial","clear",Obj("changes",entries,"summary",restored?"No patch changes retained":"Partial patch retained; recovery required"),error is OperationCanceledException?null:"Patch rejected or interrupted");
      } finally { foreach(var f in opened) if(f!=null) f.Dispose(); foreach(var h in parents) h.Dispose(); }
    }
    static Dictionary<string,object> Command(Config c) {
      if(cancelled) return Result("Cancelled","none","clear",null);
      var parents=LockParents((c.sources??new Source[0]).Select(x=>Target(c,x.path)).Concat(new string[] {c.executable}));
      var sources=new List<FileStream>();
      try {
      foreach(var source in c.sources??new Source[0]) {
        string path=Target(c,source.path);
        if((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0) throw new IOException("linked source");
        var s=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);sources.Add(s);
        Native.FileInfo info;if(!Native.GetFileInformationByHandle(s.SafeFileHandle,out info) || info.links!=1 || (info.attributes&0x400)!=0) throw new IOException("shared source");
        using(var sha=SHA256.Create()) if(BitConverter.ToString(sha.ComputeHash(s)).Replace("-","").ToLowerInvariant()!=source.sha256) throw new IOException("source changed");
      }
      using(var executable=new FileStream(c.executable,FileMode.Open,FileAccess.Read,FileShare.Read)) {
        using(var sha=SHA256.Create()) if(BitConverter.ToString(sha.ComputeHash(executable)).Replace("-","").ToLowerInvariant()!=c.executable_sha256) throw new IOException("executable changed");
        using(var job=new Job(c,dir)) {
          var identity=Obj("pid",job.Pid,"creation_time",job.CreationTime.ToString(),"executable",c.executable,"launch_id",c.run_id);
          Save("journal.json",Obj("kind","command","identity",identity,"phase","created"));
          if(cancelled) { job.Stop(); return Result("Cancelled","none",job.Drain()?"clear":"unknown",identity); }
          Save("journal.json",Obj("kind","command","identity",identity,"phase","starting"));
          job.Resume();
          Save("journal.json",Obj("kind","command","identity",identity,"phase","running"));
          var watch=Stopwatch.StartNew(); string reason=null;
          while(job.Active>0) {
            if(cancelled) reason="cancelled";
            else if(job.OutputExceeded) reason="output_limit";
            else if(watch.ElapsedMilliseconds>=c.timeout_ms) reason="timeout";
            else if(job.RootExited && job.Active>0) reason="descendants_after_exit";
            if(reason!=null) { job.Stop(); break; }
            Thread.Sleep(20);
          }
          bool drained=job.Drain();
          if(job.OutputExceeded && reason==null) reason="output_limit";
          int code=job.ExitCode;
          var result=Result(!drained?"Failed":reason=="cancelled"?"Cancelled":reason==null&&code==0?"Completed":"Failed",
            !drained?"unknown":reason==null&&code==0?"applied":"partial",drained?"clear":"unknown",
            Obj("identity",identity,"exit_code",code,"reason",reason,"tree_empty",drained,"output_bytes",job.OutputBytes,
              "stdout_tail",Tail(Path.Combine(dir,"stdout.log")),"stderr_tail",Tail(Path.Combine(dir,"stderr.log")),"summary",reason??("Process exited "+code)));
          Save("journal.json",Obj("kind","command","identity",identity,"phase",drained?"stopped":"unknown","result",result));
          return result;
        }
      }
      } finally { foreach(var s in sources) s.Dispose();foreach(var h in parents) h.Dispose(); }
    }
    static string Tail(string path) {
      using(var s=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite)) {
        s.Position=Math.Max(0,s.Length-16384); byte[] bytes=new byte[s.Length-s.Position]; s.Read(bytes,0,bytes.Length); return Encoding.UTF8.GetString(bytes);
      }
    }
  }
  sealed class Job : IDisposable {
    IntPtr job, process, thread;
    readonly List<Task> pumps=new List<Task>(); readonly object outputLock=new object();
    readonly int limit; long bytes; volatile bool exceeded;
    public int Pid; public long CreationTime;
    public long OutputBytes { get { return Interlocked.Read(ref bytes); } }
    public bool OutputExceeded { get { return exceeded; } }
    public bool RootExited { get { return Native.WaitForSingleObject(process,0)==0; } }
    public int ExitCode { get { uint code; if(!Native.GetExitCodeProcess(process,out code)) throw new Win32Exception(); return unchecked((int)code); } }
    public int Active { get { Native.Accounting info; if(!Native.QueryInformationJobObject(job,1,out info,Marshal.SizeOf(typeof(Native.Accounting)),IntPtr.Zero)) throw new Win32Exception(); return (int)info.active; } }
    static string Quote(string arg) {
      var b=new StringBuilder("\""); int slashes=0;
      foreach(char ch in arg) { if(ch=='\\') { slashes++; continue; } if(ch=='\"') b.Append('\\',slashes*2+1); else b.Append('\\',slashes); slashes=0; b.Append(ch); }
      b.Append('\\',slashes*2); return b.Append('"').ToString();
    }
    public Job(Config c,string dir) {
      limit=c.output_bytes;
      IntPtr attrs=IntPtr.Zero, jobs=IntPtr.Zero, handles=IntPtr.Zero, env=IntPtr.Zero;
      IntPtr stdout=IntPtr.Zero,stderr=IntPtr.Zero,stdin=IntPtr.Zero;
      try {
        job=Native.CreateJobObject(IntPtr.Zero,"Local\\Nirai-v2-run-"+c.run_id); if(job==IntPtr.Zero || Marshal.GetLastWin32Error()==183) throw new Win32Exception();
        var limits=new Native.ExtendedLimits(); limits.basic.flags=0x2000;
        if(!Native.SetInformationJobObject(job,9,ref limits,Marshal.SizeOf(typeof(Native.ExtendedLimits)))) throw new Win32Exception();
        stdout=Pipe(Path.Combine(dir,"stdout.log")); stderr=Pipe(Path.Combine(dir,"stderr.log"));
        var sa=new Native.Security { length=Marshal.SizeOf(typeof(Native.Security)),inherit=1 };
        stdin=Native.CreateFileRaw("NUL",0x80000000,3,ref sa,3,0,IntPtr.Zero);
        if(stdin==new IntPtr(-1)) throw new Win32Exception();
        IntPtr size=IntPtr.Zero; Native.InitializeProcThreadAttributeList(IntPtr.Zero,2,0,ref size);
        attrs=Marshal.AllocHGlobal(size); if(!Native.InitializeProcThreadAttributeList(attrs,2,0,ref size)) throw new Win32Exception();
        jobs=Marshal.AllocHGlobal(IntPtr.Size); Marshal.WriteIntPtr(jobs,job);
        if(!Native.UpdateProcThreadAttribute(attrs,0,new IntPtr(0x2000D),jobs,new IntPtr(IntPtr.Size),IntPtr.Zero,IntPtr.Zero)) throw new Win32Exception();
        handles=Marshal.AllocHGlobal(IntPtr.Size*3); Marshal.WriteIntPtr(handles,stdin); Marshal.WriteIntPtr(handles,IntPtr.Size,stdout); Marshal.WriteIntPtr(handles,IntPtr.Size*2,stderr);
        if(!Native.UpdateProcThreadAttribute(attrs,0,new IntPtr(0x20002),handles,new IntPtr(IntPtr.Size*3),IntPtr.Zero,IntPtr.Zero)) throw new Win32Exception();
        var si=new Native.StartupEx(); si.startup.cb=Marshal.SizeOf(typeof(Native.StartupEx)); si.startup.flags=0x100;
        si.startup.stdin=stdin;si.startup.stdout=stdout;si.startup.stderr=stderr;si.attributes=attrs;
        string environment=String.Join("\0",c.env.OrderBy(x=>x.Key,StringComparer.OrdinalIgnoreCase).Select(x=>x.Key+"="+x.Value))+"\0\0";
        env=Marshal.StringToHGlobalUni(environment);
        Native.ProcessInfo pi;
        string command=Quote(c.executable)+" "+String.Join(" ",c.argv.Select(Quote));
        if(!Native.CreateProcess(c.executable,new StringBuilder(command),IntPtr.Zero,IntPtr.Zero,true,0x08080404,env,c.root,ref si,out pi)) throw new Win32Exception();
        process=pi.process;thread=pi.thread;Pid=(int)pi.pid;
        long exit,kernel,user; if(!Native.GetProcessTimes(process,out CreationTime,out exit,out kernel,out user)) throw new Win32Exception();
      } catch { Dispose(); throw; }
      finally {
        if(stdout!=IntPtr.Zero) Native.CloseHandle(stdout); if(stderr!=IntPtr.Zero) Native.CloseHandle(stderr); if(stdin!=IntPtr.Zero && stdin!=new IntPtr(-1)) Native.CloseHandle(stdin);
        if(attrs!=IntPtr.Zero) { Native.DeleteProcThreadAttributeList(attrs); Marshal.FreeHGlobal(attrs); }
        if(jobs!=IntPtr.Zero) Marshal.FreeHGlobal(jobs);if(handles!=IntPtr.Zero) Marshal.FreeHGlobal(handles);if(env!=IntPtr.Zero) Marshal.FreeHGlobal(env);
      }
    }
    IntPtr Pipe(string path) {
      IntPtr read,write; var sa=new Native.Security { length=Marshal.SizeOf(typeof(Native.Security)),inherit=1 };
      if(!Native.CreatePipe(out read,out write,ref sa,0)) throw new Win32Exception();
      Native.SetHandleInformation(read,1,0);
      pumps.Add(Task.Run(()=> {
        using(var input=new FileStream(new SafeFileHandle(read,true),FileAccess.Read))
        using(var output=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.Read)) {
          byte[] buffer=new byte[8192];int count;
          while((count=input.Read(buffer,0,buffer.Length))>0) {
            lock(outputLock) {
              int accepted=(int)Math.Min(count,Math.Max(0,limit-bytes));
              if(accepted>0) { output.Write(buffer,0,accepted); bytes+=accepted; }
              if(accepted<count) exceeded=true;
            }
          }
          output.Flush(true);
        }
      })); return write;
    }
    public void Resume() { if(Native.ResumeThread(thread)==0xFFFFFFFF) throw new Win32Exception(); }
    public void Stop() { if(!Native.TerminateJobObject(job,1)) throw new Win32Exception(); }
    public bool Drain() {
      var watch=Stopwatch.StartNew(); while(Active>0 && watch.ElapsedMilliseconds<5000) Thread.Sleep(20);
      return Active==0 && Task.WaitAll(pumps.ToArray(),5000);
    }
    public void Dispose() { if(job!=IntPtr.Zero) { Native.CloseHandle(job);job=IntPtr.Zero; } if(thread!=IntPtr.Zero) { Native.CloseHandle(thread);thread=IntPtr.Zero; } if(process!=IntPtr.Zero) { Native.CloseHandle(process);process=IntPtr.Zero; } }
  }
  static class Native {
    [StructLayout(LayoutKind.Sequential)] internal struct Security { public int length;public IntPtr descriptor;public int inherit; }
    [StructLayout(LayoutKind.Sequential)] internal struct Startup { public int cb;public IntPtr reserved,desktop,title;public int x,y,xsize,ysize,xcount,ycount,fill,flags;public short show,reserved2;public IntPtr reserved3,stdin,stdout,stderr; }
    [StructLayout(LayoutKind.Sequential)] internal struct StartupEx { public Startup startup;public IntPtr attributes; }
    [StructLayout(LayoutKind.Sequential)] internal struct ProcessInfo { public IntPtr process,thread;public uint pid,tid; }
    [StructLayout(LayoutKind.Sequential)] internal struct BasicLimits { public long processTime,jobTime;public uint flags;public UIntPtr min,max;public uint active;public UIntPtr affinity;public uint priority,scheduling; }
    [StructLayout(LayoutKind.Sequential)] internal struct ExtendedLimits { public BasicLimits basic;public ulong rOps,wOps,oOps,rBytes,wBytes,oBytes;public UIntPtr processMemory,jobMemory,peakProcessMemory,peakJobMemory; }
    [StructLayout(LayoutKind.Sequential)] internal struct Accounting { public long user,kernel,periodUser,periodKernel;public uint faults,total,active,terminated; }
    [StructLayout(LayoutKind.Sequential)] internal struct FileInfo { public uint attributes;public System.Runtime.InteropServices.ComTypes.FILETIME created,accessed,written;public uint volume,sizeHigh,sizeLow,links,indexHigh,indexLow; }
    [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] internal static extern SafeFileHandle CreateFile(string path,uint access,uint share,IntPtr sa,uint mode,uint flags,IntPtr template);
    [DllImport("kernel32.dll",EntryPoint="CreateFileW",SetLastError=true,CharSet=CharSet.Unicode)] internal static extern IntPtr CreateFileRaw(string path,uint access,uint share,ref Security sa,uint mode,uint flags,IntPtr template);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool GetFileInformationByHandle(SafeFileHandle h,out FileInfo info);
    [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetFileInformationByHandle(SafeFileHandle h,int type,ref int data,uint size);
    internal static bool MarkDelete(SafeFileHandle h) { int yes=1;return SetFileInformationByHandle(h,4,ref yes,4); }
    [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] internal static extern IntPtr CreateJobObject(IntPtr sa,string name);
    [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] internal static extern IntPtr OpenJobObject(uint access,bool inherit,string name);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool SetInformationJobObject(IntPtr h,int type,ref ExtendedLimits info,int size);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool QueryInformationJobObject(IntPtr h,int type,out Accounting info,int size,IntPtr length);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool TerminateJobObject(IntPtr h,uint code);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
    [DllImport("kernel32.dll")] internal static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] internal static extern bool CreateProcess(string exe,StringBuilder args,IntPtr pa,IntPtr ta,bool inherit,uint flags,IntPtr env,string cwd,ref StartupEx startup,out ProcessInfo info);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool GetProcessTimes(IntPtr process,out long creation,out long exit,out long kernel,out long user);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool GetExitCodeProcess(IntPtr process,out uint code);
    [DllImport("kernel32.dll")] internal static extern uint WaitForSingleObject(IntPtr h,uint time);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool CreatePipe(out IntPtr read,out IntPtr write,ref Security sa,uint size);
    [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool SetHandleInformation(IntPtr h,uint mask,uint flags);
  }
}
