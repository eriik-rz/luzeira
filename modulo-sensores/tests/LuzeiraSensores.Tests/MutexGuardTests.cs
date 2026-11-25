using System.Security.AccessControl;
using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>Los nombres de los mutex de LHM ocupados antes por otro (revisión, MEDIA 1).
/// Con nombres <c>Local\</c> inventados: los de verdad son <c>Global\</c> y compartidos.</summary>
public sealed class MutexGuardTests
{
    private static string[] Names(int count = 3)
    {
        string prefix = @"Local\luzeira-test-" + Guid.NewGuid().ToString("N");
        return Enumerable.Range(0, count).Select(i => $"{prefix}-{i}").ToArray();
    }

    [Fact]
    public void Free_names_are_created_and_held()
    {
        string[] names = Names();
        using (MutexGuard guard = MutexGuard.Acquire(names, pciName: names[1]))
        {
            Assert.Empty(guard.Hostile);
            Assert.NotNull(guard.Pci);
            // Otro programa (LHM, FanControl) los abre igual: control total para «Todos».
            foreach (string name in names)
            {
                Assert.True(Mutex.TryOpenExisting(name, out Mutex? other));
                other!.Dispose();
            }
        }

        // Al soltarlos, desaparecen.
        Assert.False(Mutex.TryOpenExisting(names[0], out _));
    }

    [Fact]
    public void An_event_with_the_name_is_hostile_and_does_not_throw()
    {
        string[] names = Names();
        using var squatter = new EventWaitHandle(false, EventResetMode.ManualReset, names[1]);
        using MutexGuard guard = MutexGuard.Acquire(names, pciName: names[1]);
        Assert.Equal([names[1]], guard.Hostile);
        Assert.Null(guard.Pci);
    }

    [Fact]
    public void A_mutex_that_denies_us_is_hostile()
    {
        string[] names = Names();
        var deny = new MutexSecurity();
        deny.SetSecurityDescriptorSddlForm("D:(D;;GA;;;WD)");
        using Mutex squatter = MutexAcl.Create(false, names[2], out bool created, deny);
        Assert.True(created);
        using MutexGuard guard = MutexGuard.Acquire(names, pciName: names[1]);
        Assert.Equal([names[2]], guard.Hostile);
        Assert.NotNull(guard.Pci);
    }

    [Fact]
    public void An_existing_friendly_mutex_is_shared()
    {
        // Si LHM o FanControl ya lo crearon (con «Todos»), no es hostil.
        string[] names = Names(1);
        var everyone = new MutexSecurity();
        everyone.SetSecurityDescriptorSddlForm("D:(A;;GA;;;WD)");
        using Mutex first = MutexAcl.Create(false, names[0], out _, everyone);
        using MutexGuard guard = MutexGuard.Acquire(names, pciName: names[0]);
        Assert.Empty(guard.Hostile);
        Assert.NotNull(guard.Pci);
    }

    [Fact]
    public void The_lhm_names_are_the_five_of_mutexes_cs()
    {
        Assert.Equal(
            [@"Global\Access_ISABUS.HTP.Method", @"Global\Access_PCI", @"Global\Access_EC", @"Global\RazerReadWriteGuardMutex", @"Global\Access_USB_Sensors"],
            MutexGuard.LhmNames);
        Assert.Contains(MutexGuard.PciName, MutexGuard.LhmNames);
    }
}
