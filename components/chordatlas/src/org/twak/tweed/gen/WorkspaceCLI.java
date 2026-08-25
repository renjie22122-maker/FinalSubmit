package org.twak.tweed.gen;

import java.awt.Color;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

import org.twak.tweed.TweedSettings;

import com.thoughtworks.xstream.XStream;
import com.thoughtworks.xstream.converters.reflection.PureJavaReflectionProvider;

/**
 * Writes a complete, default-valued ChordAtlas workspace descriptor without
 * starting jMonkeyEngine. This is the stable hand-off used by myProject.
 */
public class WorkspaceCLI {

	private static void usage() {
		System.err.println( "usage: WorkspaceCLI <workspace> <footprints.obj> <minimesh-dir-or-> "
				+ "<panos-dir-or-> <origin-lat> <origin-lon> <bikegan-root> "
				+ "<facade-pytorch-root> <conda.exe> <conda-env>" );
	}

	private static File relativeChild( File workspace, String value, boolean directory ) throws Exception {
		File relative = new File( value );
		if ( relative.isAbsolute() || value.contains( ".." ) )
			throw new IllegalArgumentException( "workspace asset must be a safe relative path: " + value );

		File child = new File( workspace, value ).getCanonicalFile();
		if ( !child.toPath().startsWith( workspace.getCanonicalFile().toPath() ) )
			throw new IllegalArgumentException( "workspace asset escapes workspace: " + value );
		if ( directory ? !child.isDirectory() : !child.isFile() )
			throw new IllegalArgumentException( "workspace asset not found: " + child );
		return relative;
	}

	public static void main( String[] args ) throws Exception {
		if ( args.length != 10 ) {
			usage();
			System.exit( 2 );
		}

		File workspace = new File( args[ 0 ] ).getCanonicalFile();
		if ( !workspace.isDirectory() && !workspace.mkdirs() )
			throw new IllegalArgumentException( "cannot create workspace: " + workspace );

		File footprints = relativeChild( workspace, args[ 1 ], false );
		File miniRoot = "-".equals( args[ 2 ] ) ? null : relativeChild( workspace, args[ 2 ], true );
		File panoRoot = "-".equals( args[ 3 ] ) ? null : relativeChild( workspace, args[ 3 ], true );
		double originLat = Double.parseDouble( args[ 4 ] );
		double originLon = Double.parseDouble( args[ 5 ] );
		if ( !Double.isFinite( originLat ) || !Double.isFinite( originLon ) )
			throw new IllegalArgumentException( "local origin must be finite" );

		TweedSettings settings = new TweedSettings();
		settings.bikeGanRoot = new File( args[ 6 ] ).getCanonicalPath();
		settings.facadePytorchRoot = new File( args[ 7 ] ).getCanonicalPath();
		settings.condaExecutable = new File( args[ 8 ] ).getCanonicalPath();
		settings.condaEnvironment = args[ 9 ];
		settings.importMiniMeshTextures = true;

		GISGen gis = new GISGen();
		gis.name = "gis(o) " + footprints.getName();
		gis.color = new Color( 255, 170, 0 );
		gis.objFile = footprints;
		gis.satelliteMeshOnSelect = miniRoot == null;
		settings.genList.add( gis );

		if ( miniRoot != null ) {
			MiniGen mini = new MiniGen();
			mini.name = miniRoot.getName();
			mini.color = new Color( 0, 162, 255 );
			mini.root = miniRoot;
			settings.genList.add( mini );
		}

		if ( panoRoot != null ) {
			PanoGen panos = new PanoGen();
			panos.name = "panos " + panoRoot.getName();
			panos.color = new Color( 255, 0, 170 );
			panos.folder = panoRoot;
			panos.sourceCRS = "EPSG:4326";
			panos.meshPipelineLocalCoordinates = true;
			panos.localOriginLat = originLat;
			panos.localOriginLon = originLon;
			settings.genList.add( panos );
		}

		File target = new File( workspace, "tweed.xml" );
		File temporary = new File( workspace, "tweed.xml.part" );
		try ( FileOutputStream stream = new FileOutputStream( temporary ) ) {
			new XStream( new PureJavaReflectionProvider() ).toXML( settings, stream );
		}

		try {
			Files.move( temporary.toPath(), target.toPath(),
					StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE );
		} catch ( AtomicMoveNotSupportedException ex ) {
			Files.move( temporary.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING );
		}
		System.out.println( "workspace descriptor written to " + target );
	}
}
