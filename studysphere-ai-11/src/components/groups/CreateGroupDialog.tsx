import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Plus } from "lucide-react";
import { groupsAPI } from "@/services/api"; // Ensure this path matches your API file
import { toast } from "sonner"; // Using Sonner for nice alerts

export function CreateGroupDialog({ onGroupCreated }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [name, setName] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      console.log("Creating group...", { name, subject, description });
      // 1. Send data to Backend
      // join_code is generated server-side (M4 WP5). This used to send a
      // Math.random() code, which is not a CSPRNG and was the group's only
      // access credential.
      await groupsAPI.create({ name, subject, description });
      
      // 2. Success!
      toast.success("Group created successfully!");
      setOpen(false);
      
      // Reset form
      setName("");
      setSubject("");
      setDescription("");
      
      // 3. Refresh Parent List
      if (onGroupCreated) {
        onGroupCreated();
      }

    } catch (error) {
      console.error("Failed to create group:", error);
      toast.error("Failed to create group. Check console.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="w-full sm:w-auto gap-2 bg-primary hover:bg-primary/90 text-primary-foreground">
          <Plus className="h-4 w-4" /> Create Group
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Create Study Group</DialogTitle>
          <DialogDescription>
            Start a new learning community.
          </DialogDescription>
        </DialogHeader>
        
        <form onSubmit={handleSubmit} className="grid gap-4 py-4">
          <div className="grid gap-2">
            <Label htmlFor="name">Group Name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. React Rebels" required />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="subject">Subject</Label>
            {/* If you want a dropdown, use Select. For now, simple Input is safer to avoid errors */}
            <Input id="subject" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. Computer Science" required />
          </div>

          <div className="grid gap-2">
            <Label htmlFor="description">Description</Label>
            <Textarea id="description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What will you study?" required />
          </div>

          <Button type="submit" disabled={loading}>
            {loading ? "Creating..." : "Create Group"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}